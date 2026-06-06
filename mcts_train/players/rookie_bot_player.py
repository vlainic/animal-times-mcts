"""
Rookie baseline policy — Python port of ``Players/Rookie/rookie_bot_player.gd``.

**Behavior (high level)**

Mirrors the Godot ``RookieBotPlayer`` flow:

1. **REINFORCE** — Compute weighted attack options (:meth:`_calculate_weighted_attacks`), pick one
   stochastically (:meth:`_select_best_attack`), store as ``_stored_attack``, then
   :meth:`_smart_consolidate_one` repeatedly moves a single army from an allied neighbor into
   the planned attacker until it has ≥4 units or no moves remain; then :class:`EndReinforce`.
2. **ATTACK** — Issue :class:`Combat` for ``_stored_attack`` if still legal; otherwise recompute;
   if impossible, :class:`EndAttack`.
3. **DEPLOY** — Weighted random owned territory per pending army (:meth:`_deploy`), using
   ``DEPLOY_MULTIPLIER`` when mission factor > 1 (same as GDScript).
4. **FORTIFY** — First legal pair in nested-loop order with ``|Δunits|>1`` gets one
   :class:`MoveUnits`; else :class:`EndFortify`.

**RNG**

Pass a ``numpy.random.Generator``; it replaces Godot ``randf()`` / ``randi`` usage.

**Stateful fields**

Call :meth:`reset_for_new_turn` when the **active seat changes** (e.g. at turn boundaries in
your driver). That clears ``_stored_attack`` / weights so reinforce replans for the new
player — **do not** reset between every micro-action of the same seat’s reinforce.

**Simulator parity**

Default :attr:`mcts_train.simulator.Simulator.combat_one_round_only` is **true** (Godot Rookie’s
``one_round_only``): each ``Combat`` advances ATTACK → DEPLOY. Set the simulator to **false**
to allow clean **overruns** to stay in ATTACK with ``post_conquest_mode`` and own-tile
``MoveUnits``; this bot uses :attr:`~mcts_train.simulator.Simulator.combat_one_round_only` on
every ``Combat``. Overrun consolidation matches Godot ``handle_overrun``: **one** legal
``MoveUnits(attacker, conquered, units[attacker]-1)`` from the simulator (same bulk transfer as
GDScript); other post-conquest own-tile shuffles remain single-army moves.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..map_data import MapData
from ..missions import MissionSpec
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

# --- Same constants as rookie_bot_player.gd ---
MISSION_FACTOR = 2
DEPLOY_MULTIPLIER = 5


@dataclass
class RookieBotPlayer:
    """
    One-seat policy object bound to a :class:`Simulator` (for ``sim.m`` and ``legal_actions``).

    Attributes:
        seat: Player index 0..P-1 this bot controls.
        sim: Environment used for legality checks and neighbor queries.
        _stored_attack: Planned (attacker_idx, defender_idx) from reinforce, reused in attack.
        _weighted_options: Last computed attack option list with probabilities.
        _attacks_this_turn: Combats issued this ATTACK phase; capped at 3 (GDScript parity).
        _fortify_ij / _fortify_started: Reserved for future fortify state (currently unused).
    """

    seat: int
    sim: Simulator
    _stored_attack: Optional[Tuple[int, int]] = None
    _weighted_options: List[Dict[str, Any]] = field(default_factory=list)
    _attacks_this_turn: int = 0
    _fortify_ij: Tuple[int, int] = (0, 0)
    _fortify_started: bool = False

    def reset_for_new_turn(self) -> None:
        """Clear planned attack / weights when a **new** seat becomes active (turn change)."""
        self._stored_attack = None
        self._weighted_options.clear()
        self._attacks_this_turn = 0
        self._fortify_ij = (0, 0)
        self._fortify_started = False

    def choose_action(self, state: GameState, rng: np.random.Generator) -> Optional[Action]:
        """
        Return **one** legal action for this bot, or ``None`` if not our turn / game over.

        Args:
            state: Live game state (mutated by driver after each apply).
            rng: Generator for stochastic attack / deploy picks.

        Returns:
            A concrete ``simulator.Action``, or ``None``.
        """
        if state.winner is not None or state.phase == GamePhase.GAME_OVER:
            return None
        if state.current_player_seat() != self.seat:
            return None
        m = self.sim.m
        if state.phase == GamePhase.REINFORCE:
            return self._reinforce(state, m, rng)
        if state.phase == GamePhase.ATTACK:
            return self._attack(state, m, rng)
        if state.phase == GamePhase.DEPLOY:
            return self._deploy(state, m, rng)
        if state.phase == GamePhase.FORTIFY:
            return self._fortify(state, m, rng)
        return None

    # -------------------------------------------------------------------------
    # Mission weighting (integer factors for attack scoring — not the 0/0.5/1 tensor)
    # -------------------------------------------------------------------------

    def _mission_spec(self, state: GameState) -> MissionSpec:
        """This seat's ``MissionSpec``."""
        return state.missions[self.seat]

    def _get_territory_continent(self, territory_idx: int, m: MapData) -> str:
        """Continent name for tile index."""
        return m.territory_continent[territory_idx]

    def _owned_indexes(self, state: GameState, m: MapData) -> List[int]:
        """All territory indices owned by this bot."""
        return [t for t in range(m.T) if state.owners[t] == self.seat]

    def _is_legal_attacker(self, state: GameState, tidx: int) -> bool:
        """AoD allows 1-unit attackers; otherwise need >1 unit."""
        if state.attack_of_despair:
            return True
        return int(state.units[tidx]) > 1

    def _calculate_mission_factor(self, state: GameState, m: MapData, defender_idx: int) -> int:
        """
        Integer multiplier (1 or ``MISSION_FACTOR``) for defender tile ``defender_idx``.

        Branches match GDScript ``_calculate_mission_factor``.
        """
        spec = self._mission_spec(state)
        if spec.mission_type == "none":
            return 1
        if spec.mission_type == "elimination":
            return self._calculate_elimination_mission_factor(state, m, defender_idx)
        if spec.mission_type == "conquest":
            if spec.any_third:
                return self._get_continent_of_choice_mission_factor(state, m, defender_idx)
            return MISSION_FACTOR if self._is_territory_in_mission_continent(m, spec, defender_idx) else 1
        if spec.mission_type == "special" and spec.mission_id == "sTriple":
            dc = self._get_territory_continent(defender_idx, m)
            return self._get_three_continents_mission_factor(state, m, dc)
        return 1

    def _is_territory_in_mission_continent(self, m: MapData, spec: MissionSpec, tidx: int) -> bool:
        """True if defender's continent is listed in a conquest mission."""
        if spec.mission_type != "conquest":
            return False
        return self._get_territory_continent(tidx, m) in spec.continents

    def _find_target_seat(self, state: GameState) -> int:
        """Seat of live player matching elimination ``target_animal``, or ``-1``."""
        spec = self._mission_spec(state)
        if spec.mission_type != "elimination":
            return -1
        tgt = spec.target_animal.lower()
        for s in range(state.num_players):
            if state.eliminated[s]:
                continue
            if state.player_names[s] == tgt:
                return s
        return -1

    def _is_territory_owned_by_player(self, state: GameState, tidx: int, player_seat: int) -> bool:
        """Ownership test for elimination targeting."""
        return int(state.owners[tidx]) == player_seat

    def _get_player_territory_count(self, state: GameState, m: MapData) -> int:
        """How many tiles this bot owns."""
        return len(self._owned_indexes(state, m))

    def _get_target_player_territory_count(self, state: GameState, m: MapData) -> int:
        """How many tiles the elimination target owns."""
        spec = self._mission_spec(state)
        if spec.mission_type != "elimination":
            return 0
        ts = self._find_target_seat(state)
        if ts < 0:
            return 0
        return sum(1 for t in range(m.T) if state.owners[t] == ts)

    def _calculate_elimination_mission_factor(self, state: GameState, m: MapData, defender_idx: int) -> int:
        """
        Elimination mission weight: first-target always boosts target-owned defenders;
        otherwise compare target territory count vs distance-to-20 fallback (GDScript parity).
        """
        spec = self._mission_spec(state)
        if spec.mission_type != "elimination":
            return 1
        ts = self._find_target_seat(state)
        if ts < 0:
            return 1
        if spec.is_first_target:
            return MISSION_FACTOR if self._is_territory_owned_by_player(state, defender_idx, ts) else 1
        my_n = self._get_player_territory_count(state, m)
        tgt_n = self._get_target_player_territory_count(state, m)
        territories_to_20 = spec.fallback_territories - my_n
        if tgt_n > territories_to_20:
            return 1
        return MISSION_FACTOR if self._is_territory_owned_by_player(state, defender_idx, ts) else 1

    def _get_all_continents(self, m: MapData) -> List[str]:
        """List of continent names (stable order from map JSON)."""
        return list(m.ALL_CONTINENTS)

    def _get_continent_territories(self, m: MapData, continent_name: str) -> List[int]:
        """Territory indices belonging to a continent."""
        return [i for i in range(m.T) if m.territory_continent[i] == continent_name]

    def _count_owned_per_continent(self, state: GameState, m: MapData) -> Dict[str, int]:
        """Owned tile counts per continent for this bot."""
        out = {c: 0 for c in m.ALL_CONTINENTS}
        for t in self._owned_indexes(state, m):
            out[m.territory_continent[t]] += 1
        return out

    def _get_three_continents_mission_factor(self, state: GameState, m: MapData, defender_continent: str) -> int:
        """
        ``sTriple`` mission: boost defenders on the three continents with **least** tiles still
        needed for full control (GDScript ``_get_three_continents_mission_factor``).
        """
        owned_per = self._count_owned_per_continent(state, m)
        missing = []
        for c in self._get_all_continents(m):
            tot = len(self._get_continent_territories(m, c))
            missing.append((tot - owned_per.get(c, 0), c))
        missing.sort(key=lambda x: x[0])
        top3 = {c for _, c in missing[:3]}
        return MISSION_FACTOR if defender_continent in top3 else 1

    def _get_continent_of_choice_mission_factor(self, state: GameState, m: MapData, defender_idx: int) -> int:
        """
        ``any_third`` conquest: immediate boost if defender lies on a listed mission continent;
        else boost only if defender’s continent is the **single** best remaining continent
        (least missing), excluding fixed mission continents from eligibility (GDScript parity).
        """
        spec = self._mission_spec(state)
        if self._is_territory_in_mission_continent(m, spec, defender_idx):
            return MISSION_FACTOR
        defender_continent = self._get_territory_continent(defender_idx, m)
        owned_per = self._count_owned_per_continent(state, m)
        eligible = list(m.ALL_CONTINENTS)
        for c in spec.continents:
            if c in eligible:
                eligible.remove(c)
        missing_per: Dict[str, int] = {}
        for c in eligible:
            tot = len(self._get_continent_territories(m, c))
            missing_per[c] = tot - owned_per.get(c, 0)
        best_c = ""
        least = 999
        for c in eligible:
            miss = missing_per.get(c, 0)
            if miss < least:
                least = miss
                best_c = c
        return MISSION_FACTOR if (best_c != "" and defender_continent == best_c) else 1

    # -------------------------------------------------------------------------
    # Attack option construction (weighted random choice)
    # -------------------------------------------------------------------------

    def _calculate_weighted_attacks(self, state: GameState, m: MapData, overrun_mode: bool) -> List[Dict[str, Any]]:
        """
        Enumerate legal (src,dst) attacks with weights = (att/def ratio) * mission_factor.

        After sorting by weight, applies tier filtering identical to GDScript (overrun vs
        normal, top-K caps), then normalizes ``probability`` fields.
        """
        opts: List[Dict[str, Any]] = []
        owned = self._owned_indexes(state, m)
        for src in owned:
            if not self._is_legal_attacker(state, src):
                continue
            au = int(state.units[src])
            for dst in m.neighbors(src):
                if state.owners[dst] == self.seat or state.owners[dst] < 0:
                    continue
                du = max(1, int(state.units[dst]))
                base_w = float(au) / float(du)
                mf = self._calculate_mission_factor(state, m, dst)
                w = base_w * mf
                opts.append(
                    {
                        "src": src,
                        "dst": dst,
                        "weight": w,
                        "base_weight": base_w,
                        "mission_factor": mf,
                    }
                )
        opts.sort(key=lambda o: o["weight"], reverse=True)
        if overrun_mode:
            opts = [o for o in opts if o["weight"] > MISSION_FACTOR][:2]
        else:
            hi = [o for o in opts if o["weight"] > MISSION_FACTOR]
            if hi:
                opts = hi[:3]
            else:
                mid = [o for o in opts if o["weight"] > 1]
                if mid:
                    opts = mid[:4]
                else:
                    opts = opts[:5]
        tw = sum(o["weight"] for o in opts)
        for o in opts:
            o["probability"] = (o["weight"] / tw) if tw > 0 else 0.0
        return opts

    def _select_best_attack(self, rng: np.random.Generator) -> Optional[Tuple[int, int]]:
        """Sample one (src,dst) from ``_weighted_options`` using cumulative probabilities."""
        if not self._weighted_options:
            return None
        r = float(rng.random())
        cum = 0.0
        for o in self._weighted_options:
            cum += float(o["probability"])
            if r <= cum:
                return int(o["src"]), int(o["dst"])
        o0 = self._weighted_options[0]
        return int(o0["src"]), int(o0["dst"])

    def _smart_consolidate_one(self, state: GameState, m: MapData) -> Optional[MoveUnits]:
        """
        Single greedy consolidation step toward ``_stored_attack`` attacker: if attacker has
        <4 units, move **one** army from a neighboring owned tile with spare (>1) if legal.
        """
        if self._stored_attack is None:
            return None
        src_att, _ = self._stored_attack
        if int(state.units[src_att]) >= 4:
            return None
        for nb in m.neighbors(src_att):
            if int(state.owners[nb]) != self.seat:
                continue
            u = int(state.units[nb])
            if u <= 1:
                continue
            mv = MoveUnits(nb, src_att, 1)
            if mv in self.sim.legal_actions(state):
                return mv
        return None

    def _find_frontier_tile(self, state: GameState, m: MapData) -> Optional[int]:
        """
        Return the owned tile with minimum BFS hop-distance to any enemy tile.

        Used by ``_fortify`` when the player is fully isolated (no direct enemy border).
        """
        owned = self._owned_indexes(state, m)
        if not owned:
            return None
        enemy_tiles = [
            t for t in range(m.T)
            if int(state.owners[t]) >= 0 and int(state.owners[t]) != self.seat
        ]
        if not enemy_tiles:
            return None
        dist: Dict[int, int] = {}
        q: deque[int] = deque()
        for t in enemy_tiles:
            dist[t] = 0
            q.append(t)
        while q:
            cur = q.popleft()
            d = dist[cur]
            for nb in m.neighbors(cur):
                if nb not in dist:
                    dist[nb] = d + 1
                    q.append(nb)
        best: Optional[int] = None
        best_d = 10**9
        for t in owned:
            d = dist.get(t, 10**9)
            if d < best_d:
                best_d = d
                best = t
        return best

    def _find_attackable_border_tile(self, state: GameState, m: MapData) -> Optional[Tuple[int, int]]:
        """
        Return ``(attacker_idx, defender_idx)`` for the owned tile adjacent to any enemy
        that has the **fewest** units — i.e. the border tile most urgently needing reinforcement.

        Used as a fallback ``_stored_attack`` when ``_calculate_weighted_attacks`` returns empty
        (all border tiles have only 1 unit so normal attack scoring skips them).
        """
        best: Optional[Tuple[int, int]] = None
        best_units = 10**9
        for t in self._owned_indexes(state, m):
            for nb in m.neighbors(t):
                if int(state.owners[nb]) >= 0 and int(state.owners[nb]) != self.seat:
                    u = int(state.units[t])
                    if u < best_units:
                        best_units = u
                        best = (t, nb)
        return best

    def _reinforce(self, state: GameState, m: MapData, rng: np.random.Generator) -> Action:
        """Plan attack + consolidate; when done, ``EndReinforce``."""
        if self._stored_attack is None:
            self._weighted_options = self._calculate_weighted_attacks(state, m, False)
            self._stored_attack = self._select_best_attack(rng)
        if self._stored_attack is None:
            # All border tiles have ≤1 unit — normal scoring skips them.
            # Fall back to the weakest border tile so _smart_consolidate_one has a target.
            self._stored_attack = self._find_attackable_border_tile(state, m)
        mv = self._smart_consolidate_one(state, m)
        if mv is not None:
            return mv
        return EndReinforce()

    def _post_conquest_slide_stored(
        self, state: GameState, m: MapData
    ) -> Optional[MoveUnits]:
        """
        After a clean overrun, return the **single bulk** slide Godot uses: all armies except
        one from the stored attacker onto the conquered neighbor, as one ``MoveUnits`` action.

        The simulator exposes this as ``MoveUnits(src, dst, units[src]-1)`` on the overrun edge
        (see :attr:`mcts_train.state.GameState.overrun_slide_from`); other own-tile moves stay +1.
        """
        if not state.post_conquest_mode or self._stored_attack is None:
            return None
        src, dst = self._stored_attack
        if int(state.owners[src]) != self.seat or int(state.owners[dst]) != self.seat:
            return None
        if dst not in m.neighbors(src):
            return None
        if int(state.units[src]) <= 1:
            return None
        n = int(state.units[src]) - 1
        mv = MoveUnits(src, dst, n)
        if mv in self.sim.legal_actions(state):
            return mv
        return None

    def _attack(self, state: GameState, m: MapData, rng: np.random.Generator) -> Action:
        """Issue stored ``Combat`` or re-plan; post-conquest slides; ``EndAttack`` if impossible.

        Chains up to 3 combats per ATTACK phase on clean overruns (GDScript parity).
        Requires ``Simulator(combat_one_round_only=False)``; with the default True setting
        the sim bumps to DEPLOY after every combat, so only one combat fires per turn.
        """
        oor = self.sim.combat_one_round_only
        slide = self._post_conquest_slide_stored(state, m)
        if slide is not None:
            return slide
        if self._attacks_this_turn >= 3:
            return EndAttack()
        if state.attack_of_despair and self._attacks_this_turn >= 1:
            return EndAttack()
        if state.post_conquest_mode:
            self._weighted_options = self._calculate_weighted_attacks(state, m, True)
            self._stored_attack = self._select_best_attack(rng)
        elif self._stored_attack is None:
            self._weighted_options = self._calculate_weighted_attacks(state, m, False)
            self._stored_attack = self._select_best_attack(rng)
        atk = self._stored_attack
        if atk is None:
            return EndAttack()
        src, dst = atk
        cmb = Combat(src, dst, oor)
        if cmb in self.sim.legal_actions(state):
            self._attacks_this_turn += 1
            return cmb
        self._weighted_options = self._calculate_weighted_attacks(
            state, m, state.post_conquest_mode
        )
        self._stored_attack = self._select_best_attack(rng)
        if self._stored_attack is None:
            return EndAttack()
        src, dst = self._stored_attack
        cmb2 = Combat(src, dst, oor)
        if cmb2 in self.sim.legal_actions(state):
            self._attacks_this_turn += 1
            return cmb2
        return EndAttack()

    def _calculate_deployment_mission_factor(self, state: GameState, m: MapData, tidx: int) -> int:
        """
        Deployment score for an **owned** tile: base mission factor on that tile plus sum of
        mission factors on **enemy** neighbors (GDScript ``_calculate_deployment_mission_factor``).
        """
        base = self._calculate_mission_factor(state, m, tidx)
        ap = 0
        for nb in m.neighbors(tidx):
            if int(state.owners[nb]) == self.seat:
                continue
            ap += self._calculate_mission_factor(state, m, nb)
        return base + ap

    def _deploy(self, state: GameState, m: MapData, rng: np.random.Generator) -> Action:
        """Weighted random ``DeployPlace`` of one army; ``EndDeploy`` if nothing pending."""
        seat = self.seat
        if int(state.pending_deploy_armies[seat]) <= 0:
            return EndDeploy()
        # opts=0 rescue: can't attack anywhere but we DO border an enemy →
        # pour new armies onto the weakest border tile so it can attack next turn.
        # (Reinforce/fortify only move one hop, so interior armies can't reach the front;
        # deploy can target any owned tile, so this is the decisive unstick.)
        if not self._calculate_weighted_attacks(state, m, False):
            border = self._find_attackable_border_tile(state, m)
            if border is not None:
                dp = DeployPlace(border[0], 1)
                if dp in self.sim.legal_actions(state):
                    return dp
        opts = []
        tw = 0.0
        for t in self._owned_indexes(state, m):
            mf = self._calculate_deployment_mission_factor(state, m, t)
            w = float(mf)
            if mf > 1:
                w *= float(DEPLOY_MULTIPLIER)
            opts.append({"t": t, "weight": w})
            tw += w
        for o in opts:
            o["probability"] = (o["weight"] / tw) if tw > 0 else 0.0
        if not opts:
            return EndDeploy()
        r = float(rng.random())
        cum = 0.0
        pick = int(opts[0]["t"])
        for o in opts:
            cum += float(o["probability"])
            if r <= cum:
                pick = int(o["t"])
                break
        dp = DeployPlace(pick, 1)
        if dp in self.sim.legal_actions(state):
            return dp
        return EndDeploy()

    def _fortify(self, state: GameState, m: MapData, rng: np.random.Generator) -> Action:
        """
        First improving move among owned pairs (nested loop order like GDScript): adjacent,
        ``|u_i - u_j| > 1``, move one army from richer to poorer; else ``EndFortify``.

        Note:
            ``rng`` is unused today (deterministic tie-breaking by loop order); kept for API
            symmetry with other phase methods.
        """
        del rng
        owned = self._owned_indexes(state, m)
        data = [(t, int(state.units[t])) for t in owned]
        n = len(data)
        for i in range(n):
            for j in range(i + 1, n):
                ti, ui = data[i]
                tj, uj = data[j]
                if tj not in m.neighbors(ti):
                    continue
                if abs(ui - uj) <= 1:
                    continue
                if ui > uj:
                    src, dst = ti, tj
                else:
                    src, dst = tj, ti
                mv = MoveUnits(src, dst, 1)
                if mv in self.sim.legal_actions(state):
                    return mv
        # Consolidation: fire when no owned tile can actually attack
        # (has ≥2 units AND borders an enemy). Covers two cases:
        #   - fully isolated (no border at all) → use _find_frontier_tile
        #   - border exists but all border tiles have 1 unit → use _find_attackable_border_tile
        # No cycle risk: imbalance loop requires |diff| > 1; a 1-unit border tile
        # won't be a source for imbalance moves, only a destination for consolidation.
        owned = self._owned_indexes(state, m)
        can_attack = any(
            int(state.units[t]) >= 2 and any(
                int(state.owners[nb]) >= 0 and int(state.owners[nb]) != self.seat
                for nb in m.neighbors(t)
            )
            for t in owned
        )
        if not can_attack:
            border = self._find_attackable_border_tile(state, m)
            frontier_idx = border[0] if border else self._find_frontier_tile(state, m)
            if frontier_idx is not None:
                for nb in m.neighbors(frontier_idx):
                    if int(state.owners[nb]) != self.seat:
                        continue
                    if int(state.units[nb]) <= 1:
                        continue
                    mv = MoveUnits(nb, frontier_idx, 1)
                    if mv in self.sim.legal_actions(state):
                        return mv
        return EndFortify()
