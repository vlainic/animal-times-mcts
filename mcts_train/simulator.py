"""
Step-based **Milos rules** game simulator for offline MCTS / RL.

**What this module owns**

- **Actions** — small frozen dataclasses (``MoveUnits``, ``Combat``, phase ends, …) that
  ``legal_actions`` emits and ``apply`` mutates on :class:`mcts_train.state.GameState`.
- **Combat** — subtraction dice, pair-wise comparisons, **mutual destruction reroll** loop
  (same idea as ``server.gd`` ``_resolve_combat_on_server``).
- **Phases** — REINFORCE (neighbor moves among own tiles), ATTACK (adjacent enemy combat),
  DEPLOY (place ``pending_deploy_armies``), FORTIFY (neighbor moves), then next seat.
- **Economy hooks** — on capture: +1 pending deploy for capturer; continent full-control
  bonus from ``CONTINENT_BONUS``; optional card draw into ``hands`` (see ``coins``).

**Intentional simplifications vs ``server.gd``**

- No **card trade** resolution in DEPLOY (only manual ``DeployPlace`` / ``EndDeploy``).
- **Player elimination** runs when a seat **loses its last territory** in combat:
  :meth:`Simulator.apply_player_elimination` merges hands to the eliminator and retargets
  elimination missions; **turn-queue pruning** on elimination matches
  ``server.gd`` ``_remove_player_from_turn_queue`` (removed seat, advance if current).
  **Territory redistribution** to eliminator is still out of scope.
- **Post-conquest troop movement** is minimal: conquered tile set to **1** unit like
  ``_handle_conquest_on_server``; no auto-slide — the overrun attacker→conquered edge gets one
  legal **bulk** ``MoveUnits`` (``units[src]-1``); other own edges in ATTACK stay +1 steps.
- **Combat auto-advance** matches ``server.gd``: when :attr:`Simulator.combat_one_round_only`
  is true (default), every ``Combat`` bumps ATTACK → DEPLOY. When false, a **clean overrun**
  (``attacker_losses == 0`` and territory conquered) keeps ATTACK and sets
  ``GameState.post_conquest_mode`` so further ``Combat`` / own moves are legal until a
  non-overrun outcome or ``EndAttack``.

**Attack of Despair**

If ``attack_of_despair`` is true and a combat changes ownership, phase jumps to DEPLOY
(anti-chain), mirroring the server’s AoD + conquest branch.

**Starting setup**

Each ``new_game`` draws fresh entropy from the OS via ``numpy.random.SeedSequence()`` and
spawns five independent streams: board shuffle + army sprinkle, mission assignment, card deck,
combat dice, and policy / bot randomness. Successive calls are not identical across processes
or time. :class:`~mcts_train.state.GameState` keeps ``rng_cards``, ``rng_dice``, and
``rng_policy`` for rollouts.

**Event log**

Set ``Simulator(..., log_events=True)`` so each ``GameState`` gets an :class:`mcts_train.state.EventLog`
with tags ``[COMBAT]``, ``[CONTINENT]`` (full continent + deploy bonus), ``[ELIM]``, ``[WIN]``
(``[WIN]`` includes mission ``title`` / ``description`` from JSON when present)
— see :meth:`Simulator._append_log`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from . import coins as coins_mod
from .map_data import MapData, get_map_data
from .missions import (
    MissionSpec,
    check_mission_won,
    elimination_mission_completed_by_eliminator,
    load_mission_pool,
    mission_from_player_dict,
    update_elimination_missions_after_elimination,
)
from .state import EventLog, GamePhase, GameState

# --- Continent bonus when a full continent is first completed by the conqueror (rules doc) ---
CONTINENT_BONUS: Dict[str, int] = {
    "Mudflats": 6,
    "Bamboovia": 8,
    "Riverside": 7,
    "Bushlands": 7,
    "Eucalypta": 4,
    "Peaks": 4,
}

# --- Classic Risk-style starting infantry per live player count (see docs/final_rules.md) ---
STARTING_ARMIES = {2: 40, 3: 35, 4: 30, 5: 25, 6: 20}


def _fresh_env_rngs() -> Tuple[
    np.random.Generator,
    np.random.Generator,
    np.random.Generator,
    np.random.Generator,
    np.random.Generator,
]:
    """
    Five independent RNG streams from OS entropy: board setup, missions, cards, dice, policy.
    """
    children = np.random.SeedSequence().spawn(5)
    return (
        np.random.default_rng(children[0]),
        np.random.default_rng(children[1]),
        np.random.default_rng(children[2]),
        np.random.default_rng(children[3]),
        np.random.default_rng(children[4]),
    )


# =============================================================================
# Actions — immutable tokens for MCTS edges (hashable for sets if needed)
# =============================================================================


@dataclass(frozen=True)
class EndReinforce:
    """Player is done repositioning; transition REINFORCE → ATTACK."""

    pass


@dataclass(frozen=True)
class MoveUnits:
    """
    Move ``count`` armies along an owned edge (REINFORCE, FORTIFY, or ATTACK post-conquest).

    Attributes:
        src: Source territory index (must have ``> count`` units).
        dst: Neighbor owned by same seat.
        count: Usually **1** in generated REINFORCE / FORTIFY / post-conquest edges; **bulk**
            ``units[src]-1`` is legal only on the directed overrun slide edge in ATTACK (see
            :attr:`mcts_train.state.GameState.overrun_slide_from`).
    """

    src: int
    dst: int
    count: int = 1


@dataclass(frozen=True)
class Combat:
    """
    One combat round: ``attacker`` (tile index) strikes adjacent ``defender``.

    Attributes:
        attacker: Attacking tile (must be current player, legal dice count).
        defender: Defending adjacent enemy tile.
        one_round_only: When true, ``apply`` forces ATTACK → DEPLOY after resolution unless
            AoD short-circuits. When false, a clean overrun keeps ATTACK (see ``Simulator``).
    """

    attacker: int
    defender: int
    one_round_only: bool = True


@dataclass(frozen=True)
class EndAttack:
    """Manual end ATTACK (e.g. no legal attacks); go to DEPLOY."""

    pass


@dataclass(frozen=True)
class DeployPlace:
    """Place ``count`` bonus armies from pending pool onto an owned territory."""

    territory: int
    count: int = 1


@dataclass(frozen=True)
class EndDeploy:
    """No pending armies left (or pass); DEPLOY → FORTIFY."""

    pass


@dataclass(frozen=True)
class EndFortify:
    """End fortify; advance turn index and wrap to next player’s REINFORCE."""

    pass


Action = Union[EndReinforce, MoveUnits, Combat, EndAttack, DeployPlace, EndDeploy, EndFortify]


# =============================================================================
# Combat kernel (Milos subtraction + mutual wipe reroll)
# =============================================================================


def _roll_sorted_dice(n: int, rng: np.random.Generator) -> List[int]:
    """Roll ``n`` d6, highest-first (same sort as server)."""
    d = [int(rng.integers(1, 7)) for _ in range(n)]
    d.sort(reverse=True)
    return d


def resolve_combat_milos(
    att_units: int,
    def_units: int,
    attack_of_despair: bool,
    rng: np.random.Generator,
) -> Tuple[int, int, bool, bool, Tuple[int, ...], Tuple[int, ...]]:
    """
    Resolve **one** combat round with Milos subtraction rules.

    Dice counts follow ``server.gd``: attacker ``max(1, min(3, att_units - 1))`` with AoD
    special-case for 1 unit on attacker; defender ``min(3, def_units)``.

    Pair highest dice: attacker wins → defender loses ``diff``; tie → both lose 1;
    defender wins → attacker loses ``|diff|``. Losses are **not** capped per die before
    totals; then if **both** would end ≤0, re-roll the **entire** round (while-loop).

    Returns:
        ``(att_after, def_after, territory_conquered, defender_counter_conquered,
        attacker_dice_high_first, defender_dice_high_first)`` for the **final** accepted roll
        (after any mutual-destruction rerolls).
    """
    while True:
        att_dice_n = max(1, min(3, att_units - 1))
        if attack_of_despair and att_units == 1:
            att_dice_n = 1
        def_dice_n = min(3, def_units)
        ad = _roll_sorted_dice(att_dice_n, rng)
        dd = _roll_sorted_dice(def_dice_n, rng)
        att_loss = 0
        def_loss = 0
        for i in range(min(len(ad), len(dd))):
            diff = ad[i] - dd[i]
            if diff > 0:
                def_loss += diff
            elif diff == 0:
                att_loss += 1
                def_loss += 1
            else:
                att_loss += abs(diff)
        att_final = att_units - att_loss
        def_final = def_units - def_loss
        if att_final <= 0 and def_final <= 0:
            continue
        conquered = def_final <= 0
        def_conq = att_final <= 0
        return max(0, att_final), max(0, def_final), conquered, def_conq, tuple(ad), tuple(dd)


def _check_aod(m: MapData, owners: np.ndarray, units: np.ndarray, seat: int) -> bool:
    """
    Attack of Despair: every owned territory has at most 1 army (max over tiles ≤ 1).

    Matches ``server.gd`` ``get_player_max_units`` / ``check_attack_of_despair_during_reinforce`` idea.
    """
    mx = 0
    for t in range(m.T):
        if owners[t] == seat:
            mx = max(mx, int(units[t]))
    return mx <= 1


def _continent_just_completed(
    m: MapData, owners: np.ndarray, prev_owners: np.ndarray, conqueror: int, territory_idx: int
) -> Optional[str]:
    """
    If the tile at ``territory_idx`` flipping to ``conqueror`` **completed** a full continent
    for them (was not full before), return that continent’s name for bonus lookup.

    Args:
        prev_owners: Snapshot before this combat’s ownership writes (caller passes copy).
    """
    c = m.territory_continent[territory_idx]
    idxs = [i for i in range(m.T) if m.territory_continent[i] == c]
    if not idxs:
        return None
    if not all(owners[i] == conqueror for i in idxs):
        return None
    if all(prev_owners[i] == conqueror for i in idxs):
        return None
    return c


def _mission_win_log_detail(spec: MissionSpec) -> str:
    """
    Human-readable mission line for ``[WIN]`` (JSON ``title`` / ``description`` when present).
    """
    r = spec.raw
    title = str(r.get("title", "")).strip().replace("\n", " ")
    desc = str(r.get("description", "")).strip().replace("\n", " ")
    if title and desc:
        body = f"{title} — {desc}"
    elif title:
        body = title
    elif desc:
        body = desc
    elif spec.mission_type == "conquest":
        ch = ", ".join(spec.continents)
        suf = " plus any one full continent of your choice" if spec.any_third else ""
        body = f"Conquest: control every territory in {ch}{suf}"
    elif spec.mission_type == "elimination":
        body = (
            f"Elimination: eliminate {spec.target_animal}; if retargeted, "
            f"then {spec.fallback_territories} territories"
        )
    elif spec.mission_type == "special" and spec.territory_count > 0:
        body = f"Special: hold at least {spec.territory_count} territories"
    elif spec.mission_type == "special" and spec.continent_count > 0:
        body = f"Special: fully control {spec.continent_count} continents of your choice"
    else:
        body = "(no mission text)"
    return f"{body} | mission_id={spec.mission_id} mission_type={spec.mission_type}"


# =============================================================================
# Simulator
# =============================================================================


class Simulator:
    """
    Environment API: ``new_game``, ``legal_actions``, ``apply``, terminal helpers.

    Construct with optional custom :class:`MapData` (default ``get_map_data()``).

    Attributes:
        combat_one_round_only: If true (default), emitted ``Combat`` actions use
            ``one_round_only=True`` (Rookie / bot parity). If false, emitted combats use
            ``one_round_only=False`` so clean overruns stay in ATTACK with
            ``post_conquest_mode``.
        log_events: When true, ``new_game`` attaches an enabled :class:`mcts_train.state.EventLog`.
        max_log_lines: Cap on ``EventLog.entries`` length (oldest lines dropped).
    """

    def __init__(
        self,
        m: Optional[MapData] = None,
        *,
        combat_one_round_only: bool = True,
        log_events: bool = False,
        max_log_lines: int = 1000,
    ):
        """
        Args:
            m: Map graph (``None`` → cached global map).
            combat_one_round_only: Controls the ``Combat.one_round_only`` flag on all generated
                attack edges (see module doc).
            log_events: If true, states record combat / elimination lines on ``state.event_log``.
            max_log_lines: Maximum stored log lines per state (FIFO trim).
        """
        self.m = m or get_map_data()
        self.combat_one_round_only = combat_one_round_only
        self.log_events = log_events
        self.max_log_lines = max(16, int(max_log_lines))

    def new_game(
        self,
        num_players: int,
        player_names: Sequence[str],
        *,
        mission_pool: str = "conquest",
    ) -> GameState:
        """
        Build a random initial state for ``P`` players.

        Randomness is **internal**: a fresh ``SeedSequence`` (OS entropy) spawns streams for
        board setup, mission shuffle, card deck, combat dice, and policy / bot use.

        Args:
            num_players: ``2..6``.
            player_names: Length ``P`` animal names (lowercased internally) for elimination matching.
            mission_pool: Key in ``missions.json`` (e.g. ``conquest``), or ``\"all\"`` to merge
                ``conquest`` + ``elimination`` + ``special`` then shuffle (training / mixed missions).

        Returns:
            Fresh ``GameState`` in REINFORCE for seat ``0``.
        """
        m = self.m
        P = num_players
        assert 2 <= P <= 6
        assert len(player_names) == P

        rng_board, rng_missions, rng_cards, rng_dice, rng_policy = _fresh_env_rngs()

        all_pools = load_mission_pool()
        if mission_pool == "all":
            pool_list = []
            for key in ("conquest", "elimination", "special"):
                pool_list.extend(all_pools.get(key, []))
            if not pool_list:
                raise ValueError("mission_pool 'all' produced an empty combined list")
        elif mission_pool not in all_pools:
            raise KeyError(
                f"unknown mission_pool {mission_pool!r}; keys={sorted(all_pools.keys())} plus 'all'"
            )
        else:
            pool_list = all_pools[mission_pool]
            if not pool_list:
                raise ValueError(f"mission pool {mission_pool!r} is empty")
        raw_missions = [dict(x) for x in pool_list]
        rng_missions.shuffle(raw_missions)
        mission_dicts = [dict(raw_missions[i % len(raw_missions)]) for i in range(P)]
        missions = tuple(mission_from_player_dict(md) for md in mission_dicts)

        # --- Territory ownership: shuffle tiles, assign round-robin so everyone gets floor(T/P) ---
        territories = list(range(m.T))
        rng_board.shuffle(territories)
        owners = np.full(m.T, -1, dtype=np.int32)
        units = np.ones(m.T, dtype=np.int32)
        for i, t in enumerate(territories):
            owners[t] = i % P

        # --- Sprinkle (per_player * P - T) extra armies on random owned tiles ---
        per_player = STARTING_ARMIES.get(P, 35)
        total_pool = per_player * P - m.T
        owned_by = [[] for _ in range(P)]
        for t in range(m.T):
            owned_by[owners[t]].append(t)
        for _ in range(total_pool):
            s_i = int(rng_board.integers(0, P))
            pick = owned_by[s_i][int(rng_board.integers(0, len(owned_by[s_i])))]
            units[pick] += 1

        deck = coins_mod.create_balanced_deck(rng_cards)
        queue = tuple(range(P))
        eliminated = np.zeros(P, dtype=bool)
        pending = np.zeros(P, dtype=np.int32)
        hands: List[List] = [[] for _ in range(P)]

        st = GameState(
            map_T=m.T,
            num_players=P,
            owners=owners,
            units=units,
            player_queue=queue,
            current_turn_index=0,
            phase=GamePhase.REINFORCE,
            rng_cards=rng_cards,
            rng_dice=rng_dice,
            rng_policy=rng_policy,
            missions=missions,
            player_names=tuple(str(n).lower() for n in player_names),
            eliminated=eliminated,
            attack_of_despair=_check_aod(m, owners, units, 0),
            hands=hands,
            deck=list(deck),
            depot=[],
            pending_deploy_armies=pending,
            event_log=EventLog(enabled=self.log_events, max_lines=self.max_log_lines),
        )
        return st

    def _append_log(self, s: GameState, line: str) -> None:
        """Append one line to ``s.event_log`` (no-op when that log was created with ``enabled=False``)."""
        s.event_log.append(line)

    def _append_win_log(self, s: GameState, seat: int, reason: str) -> None:
        """``[WIN]`` with full mission title/description from ``missions.json`` when available."""
        spec = s.missions[seat]
        detail = _mission_win_log_detail(spec)
        self._append_log(
            s,
            f"[WIN] {s.player_names[seat]} (seat {seat}) {reason} | {detail}",
        )

    def legal_actions(self, s: GameState) -> List[Action]:
        """
        All legal actions for **current** seat and phase (may be large).

        REINFORCE / FORTIFY: every adjacent owned pair with ``src`` units > 1 → ``MoveUnits`` +1;
        plus a phase-end marker.

        ATTACK: every legal adjacent enemy ``Combat`` (flag from :attr:`combat_one_round_only`);
        when ``post_conquest_mode``, own ``MoveUnits`` among owned neighbors: the overrun pair
        (last attacker → conquered) allows **one** maximal ``MoveUnits(src, dst, units[src]-1)``
        (Godot bulk slide); other edges use ``count`` 1 only.

        DEPLOY: ``DeployPlace`` on each owned tile while pending > 0; else ``EndDeploy``.
        """
        m = self.m
        seat = s.current_player_seat()
        if s.phase == GamePhase.GAME_OVER or s.winner is not None:
            return []
        out: List[Action] = []

        # --- REINFORCE: single-step moves among own neighbors (leave ≥1 on source) ---
        if s.phase == GamePhase.REINFORCE:
            for src in range(m.T):
                if s.owners[src] != seat or s.units[src] <= 1:
                    continue
                for dst in m.neighbors(src):
                    if s.owners[dst] != seat:
                        continue
                    if s.units[dst] < 1:
                        continue
                    max_mv = s.units[src] - 1
                    if max_mv >= 1:
                        out.append(MoveUnits(src, dst, 1))
            out.append(EndReinforce())
            return out

        # --- ATTACK: post-conquest own moves + combats; EndAttack when stuck or finished ---
        if s.phase == GamePhase.ATTACK:
            oor = self.combat_one_round_only
            if s.post_conquest_mode:
                for src in range(m.T):
                    if s.owners[src] != seat or s.units[src] <= 1:
                        continue
                    for dst in m.neighbors(src):
                        if s.owners[dst] != seat:
                            continue
                        if s.units[dst] < 1:
                            continue
                        max_mv = int(s.units[src]) - 1
                        if max_mv < 1:
                            continue
                        if (
                            s.overrun_slide_from >= 0
                            and src == s.overrun_slide_from
                            and dst == s.overrun_slide_to
                        ):
                            out.append(MoveUnits(src, dst, max_mv))
                        else:
                            out.append(MoveUnits(src, dst, 1))
            aod = s.attack_of_despair
            for src in range(m.T):
                if s.owners[src] != seat:
                    continue
                if not aod and s.units[src] <= 1:
                    continue
                for dst in m.neighbors(src):
                    if s.owners[dst] == seat or s.owners[dst] < 0:
                        continue
                    if s.units[dst] < 1:
                        continue
                    out.append(Combat(src, dst, oor))
            if s.attack_performed_this_turn:
                out.append(EndAttack())
            elif len(out) == 0:
                out.append(EndAttack())
            return out

        # --- DEPLOY: place one army at a time on owned tiles ---
        if s.phase == GamePhase.DEPLOY:
            p = int(s.pending_deploy_armies[seat])
            if p <= 0:
                out.append(EndDeploy())
                return out
            owned = [t for t in range(m.T) if s.owners[t] == seat]
            for t in owned:
                out.append(DeployPlace(t, 1))
            if not owned:
                out.append(EndDeploy())
            return out

        # --- FORTIFY: same local moves as reinforce, then EndFortify ---
        if s.phase == GamePhase.FORTIFY:
            for src in range(m.T):
                if s.owners[src] != seat or s.units[src] <= 1:
                    continue
                for dst in m.neighbors(src):
                    if s.owners[dst] != seat:
                        continue
                    out.append(MoveUnits(src, dst, 1))
            out.append(EndFortify())
            return out

        return out

    def apply(self, s: GameState, action: Action) -> None:
        """
        Mutate ``s`` in place according to ``action`` (no return value).

        Raises:
            TypeError: Unknown action type.
            AssertionError: Illegal state / action pairing if asserts fire (training bug).
        """
        m = self.m
        seat = s.current_player_seat()

        if isinstance(action, EndReinforce):
            s.phase = GamePhase.ATTACK
            s.attack_performed_this_turn = False
            s.post_conquest_mode = False
            s.overrun_slide_from = -1
            s.overrun_slide_to = -1
            return

        if isinstance(action, MoveUnits):
            assert s.phase in (
                GamePhase.REINFORCE,
                GamePhase.FORTIFY,
            ) or (s.phase == GamePhase.ATTACK and s.post_conquest_mode)
            src, dst, n = action.src, action.dst, action.count
            assert s.owners[src] == s.owners[dst] == seat
            assert s.units[src] > n
            s.units[src] -= n
            s.units[dst] += n
            if (
                s.phase == GamePhase.ATTACK
                and s.post_conquest_mode
                and s.overrun_slide_from == src
                and s.overrun_slide_to == dst
            ):
                s.overrun_slide_from = -1
                s.overrun_slide_to = -1
            return

        if isinstance(action, Combat):
            self._apply_combat(s, m, seat, action)
            return

        if isinstance(action, EndAttack):
            s.phase = GamePhase.DEPLOY
            s.post_conquest_mode = False
            s.overrun_slide_from = -1
            s.overrun_slide_to = -1
            return

        if isinstance(action, DeployPlace):
            t = action.territory
            assert s.owners[t] == seat
            assert s.pending_deploy_armies[seat] >= action.count
            s.units[t] += action.count
            s.pending_deploy_armies[seat] -= action.count
            return

        if isinstance(action, EndDeploy):
            s.phase = GamePhase.FORTIFY
            return

        if isinstance(action, EndFortify):
            s.current_turn_index = (s.current_turn_index + 1) % len(s.player_queue)
            s.phase = GamePhase.REINFORCE
            s.attack_performed_this_turn = False
            s.post_conquest_mode = False
            s.overrun_slide_from = -1
            s.overrun_slide_to = -1
            s.captured_this_turn = False
            s.attack_of_despair = _check_aod(m, s.owners, s.units, s.current_player_seat())
            self._maybe_declare_winner(s)
            return

        raise TypeError(action)

    def apply_player_elimination(self, s: GameState, eliminated_seat: int, eliminator_seat: int) -> None:
        """
        Apply **one** player elimination (``server.gd`` ``_handle_player_elimination`` subset).

        Marks ``eliminated[eliminated_seat]``. **All** cards in ``hands[eliminated_seat]`` are
        appended to ``hands[eliminator_seat]`` (then the eliminated hand is cleared), matching
        ``_transfer_cards_from_eliminated_player``. Territory reassignment is still out of scope.

        If the **eliminator** holds an elimination mission whose **current** target is the
        eliminated seat’s name, sets ``winner`` and ``GAME_OVER``. Otherwise retargets every
        elimination mission that had targeted the eliminated player to the eliminator and sets
        ``is_first_target`` false on those specs.
        """
        if s.winner is not None or s.phase == GamePhase.GAME_OVER:
            return
        p = s.num_players
        assert 0 <= eliminated_seat < p and 0 <= eliminator_seat < p
        assert eliminated_seat != eliminator_seat
        assert not bool(s.eliminated[eliminated_seat])

        el_hand = s.hands[eliminated_seat]
        n_from_elim = len(el_hand)
        s.hands[eliminator_seat].extend(el_hand)
        el_hand.clear()
        self._append_log(
            s,
            f"[ELIM] {s.player_names[eliminated_seat]} eliminated by {s.player_names[eliminator_seat]}; "
            f"{n_from_elim} cards transferred",
        )
        elim_spec = s.missions[eliminator_seat]
        if elimination_mission_completed_by_eliminator(elim_spec, eliminated_seat, s.player_names):
            s.eliminated[eliminated_seat] = True
            s.winner = eliminator_seat
            s.phase = GamePhase.GAME_OVER
            self._append_win_log(s, eliminator_seat, "elimination_mission")
            return

        eliminated_name = str(s.player_names[eliminated_seat])
        eliminator_name = str(s.player_names[eliminator_seat])
        s.eliminated[eliminated_seat] = True
        update_elimination_missions_after_elimination(s.missions, eliminated_name, eliminator_name)
        self._remove_player_from_turn_queue(s, eliminated_seat)

    def _remove_player_from_turn_queue(self, s: GameState, eliminated_seat: int) -> None:
        """
        Remove eliminated seat from ``player_queue`` (``server.gd`` ``_remove_player_from_turn_queue``).

        If the eliminated seat was the current player, the next seat in queue becomes active
        in REINFORCE (no DEPLOY/FORTIFY for the eliminated seat). Clears pending deploy for
        the eliminated seat. Declares winner if one seat remains.
        """
        if s.winner is not None or s.phase == GamePhase.GAME_OVER:
            return
        queue = list(s.player_queue)
        try:
            eliminated_index = queue.index(eliminated_seat)
        except ValueError:
            return
        queue.pop(eliminated_index)
        s.player_queue = tuple(queue)
        s.pending_deploy_armies[eliminated_seat] = 0

        if eliminated_index < s.current_turn_index:
            s.current_turn_index -= 1
        elif eliminated_index == s.current_turn_index:
            if s.current_turn_index >= len(s.player_queue):
                s.current_turn_index = 0
            s.phase = GamePhase.REINFORCE
            s.attack_performed_this_turn = False
            s.post_conquest_mode = False
            s.overrun_slide_from = -1
            s.overrun_slide_to = -1
            s.captured_this_turn = False
            if s.player_queue:
                s.attack_of_despair = _check_aod(
                    self.m, s.owners, s.units, s.current_player_seat()
                )

        self._maybe_last_player_winner(s)

    def _maybe_last_player_winner(self, s: GameState) -> None:
        """If only one seat remains in the turn queue, they win."""
        if s.winner is not None:
            return
        if len(s.player_queue) == 1:
            w = int(s.player_queue[0])
            s.winner = w
            s.phase = GamePhase.GAME_OVER
            self._append_win_log(s, w, "last_standing")

    def _apply_combat(self, s: GameState, m: MapData, seat: int, action: Combat) -> None:
        """Internal: dice, ownership flips, pending armies, card draw, phase bump."""
        if s.overrun_slide_from >= 0:
            osf, ost = int(s.overrun_slide_from), int(s.overrun_slide_to)
            stale = (
                s.owners[osf] != seat
                or s.owners[ost] != seat
                or ost not in m.neighbors(osf)
                or int(s.units[osf]) <= 1
            )
            if stale:
                s.overrun_slide_from = -1
                s.overrun_slide_to = -1
        src, dst = action.attacker, action.defender
        prev_own = s.owners.copy()
        att_o = int(s.owners[src])
        def_o = int(s.owners[dst])
        assert att_o == seat
        assert def_o != seat and def_o >= 0
        au, du = int(s.units[src]), int(s.units[dst])
        aod = s.attack_of_despair
        att_f, def_f, conquered, def_conq, att_dice, def_dice = resolve_combat_milos(
            au, du, aod, s.rng_dice
        )
        attacker_losses = au - att_f
        is_overrun = conquered and not def_conq and attacker_losses == 0
        s.units[src] = att_f
        s.units[dst] = def_f
        s.attack_performed_this_turn = True
        continent_log: Optional[Tuple[int, str]] = None
        if conquered and not def_conq:
            s.owners[dst] = att_o
            s.units[dst] = 1
            s.captured_this_turn = True
            card = coins_mod.draw_from_deck(s.deck, s.depot, s.rng_cards)
            if card:
                s.hands[seat].append(coins_mod.path_to_token(card))
            s.pending_deploy_armies[att_o] += 1
            cname = _continent_just_completed(m, s.owners, prev_own, att_o, dst)
            if cname and cname in CONTINENT_BONUS:
                s.pending_deploy_armies[att_o] += CONTINENT_BONUS[cname]
            if cname:
                continent_log = (att_o, cname)
        elif def_conq:
            s.owners[src] = def_o
            s.units[src] = 1
            s.captured_this_turn = True
            card = coins_mod.draw_from_deck(s.deck, s.depot, s.rng_cards)
            if card:
                s.hands[def_o].append(coins_mod.path_to_token(card))
            cname = _continent_just_completed(m, s.owners, prev_own, def_o, src)
            if cname and cname in CONTINENT_BONUS:
                s.pending_deploy_armies[def_o] += CONTINENT_BONUS[cname]
            s.pending_deploy_armies[def_o] += 1
            if cname:
                continent_log = (def_o, cname)

        sn = m.territory_names[src]
        dn = m.territory_names[dst]
        ap = s.player_names[att_o]
        dp = s.player_names[def_o]
        fa = int(s.units[src])
        fd = int(s.units[dst])
        ad_s = ",".join(str(x) for x in att_dice)
        dd_s = ",".join(str(x) for x in def_dice)
        self._append_log(
            s,
            f"[COMBAT] {sn} ({ap}) vs {dn} ({dp}) dice att[{ad_s}] def[{dd_s}] "
            f"units {au}->{fa} / {du}->{fd} conquered={conquered} def_conq={def_conq} aod={aod}",
        )
        if continent_log is not None:
            cs, cnm = continent_log
            bonus = int(CONTINENT_BONUS.get(cnm, 0))
            self._append_log(
                s,
                f"[CONTINENT] {s.player_names[cs]} completed {cnm}; +{bonus} pending deploy bonus",
            )

        def _tiles(seat_idx: int) -> int:
            return int(np.sum(s.owners == seat_idx))

        if conquered and not def_conq:
            if _tiles(def_o) == 0 and not bool(s.eliminated[def_o]):
                self.apply_player_elimination(s, def_o, att_o)
        elif def_conq:
            if _tiles(att_o) == 0 and not bool(s.eliminated[att_o]):
                self.apply_player_elimination(s, att_o, def_o)

        if s.phase == GamePhase.GAME_OVER:
            return

        # Current attacker eliminated (counter-conquest): queue already advanced to REINFORCE.
        if bool(s.eliminated[seat]):
            return

        if s.attack_of_despair and (conquered or def_conq):
            s.phase = GamePhase.DEPLOY
            s.post_conquest_mode = False
            s.overrun_slide_from = -1
            s.overrun_slide_to = -1
        elif action.one_round_only:
            s.phase = GamePhase.DEPLOY
            s.post_conquest_mode = False
            s.overrun_slide_from = -1
            s.overrun_slide_to = -1
        elif is_overrun:
            s.post_conquest_mode = True
            s.overrun_slide_from = src
            s.overrun_slide_to = dst
        else:
            s.phase = GamePhase.DEPLOY
            s.post_conquest_mode = False
            s.overrun_slide_from = -1
            s.overrun_slide_to = -1

    def _maybe_declare_winner(self, s: GameState) -> None:
        """
        After a full turn (end of FORTIFY), check each seat’s ``check_mission_won``.

        First satisfied mission sets ``winner`` and ``GAME_OVER`` (simple priority by seat order).
        """
        if s.winner is not None:
            return
        for seat in range(s.num_players):
            if s.eliminated[seat]:
                continue
            if check_mission_won(
                self.m,
                s.owners,
                seat,
                s.missions[seat],
                s.player_names,
                s.eliminated,
            ):
                s.winner = seat
                s.phase = GamePhase.GAME_OVER
                self._append_win_log(s, seat, "mission_complete")
                return

    def is_terminal(self, s: GameState) -> bool:
        """True if phase is ``GAME_OVER`` or ``winner`` is set."""
        return s.phase == GamePhase.GAME_OVER or s.winner is not None
