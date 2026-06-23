**Milos**

Would those matrix-layers be enough for global risk-like state:

* map adjacency
* unit per land
* owners
* mission land
* card/coin land

do I miss any info player could be use for decision making in risk-like game?

=============================================================================

**A: Missing critical state:**

- **Current player** + turn order
- **Current phase** (reinforce/attack/deploy/fortify)
- **Cards in hand** per player (not just territory cards on map)
- **Armies to deploy** (from continent bonuses/captures)
- **Captured this turn** flag (for card drawing eligibility)
- **Attack history** this turn (for "attack ends if you lose armies" rule)

**Optional but useful:**
- Continent control status (derivable from owners + adjacency, but faster precomputed)
- Available reinforcement moves this phase (for pruning invalid actions)

Your list covers the static/spatial info well. You're missing the dynamic turn state and player-specific resources (cards, pending deployments).

=============================================================================

**R: I dont agree with almost all:**

*  for decision ITS YOUR TURN :P
* available actions and stattes will depend on phase... but this can be derived from global state
* cards in hand is only I was thinking to miss, but I would see that as global feature... so I would skip for start
* armies to deploy is very occasional
* attack history -> ah, fuck that for now
* continent control might be for global . . .
* simulator will give what is legal... no?!

note: I was thining of pure static board-game info...

=============================================================================

**A2: You're right - for pure static board state, your list covers it:**

✅ **Sufficient:**
- Map adjacency
- Units per land  
- Owners
- Mission lands
- Card/coin lands

**Derivable:**
- Phase → from turn structure
- Legal actions → simulator generates
- Continent control → from owners + adjacency

**Optional global context:**
- Cards in hand (if you want smarter trading decisions)
- Continent control flags (precomputed for speed)

Your list is complete for the board game state. Simulator handles legality, MCTS doesn't need to store it.

=============================================================================

**Milos thinking:**

Extra state info:
- how many lands for continent to capture left after target
- how many lands for defender elimination

=============================================================================

**Attack state key — continent distance buckets (added)**

Two new fields appended to the Mctsland attack state key (now a 6-tuple):

- ``att_cont_bucket``: tiles the **attacker** still needs to fully own the continent of the target
  territory — bucketed 1 (≤1 needed), 2 (exactly 2), 3 (3 or more).
- ``def_cont_bucket``: same from the **defender's** perspective (how many tiles of that continent
  the current owner still does not own).

**Attack state key — defender land count (7th field)**

- ``def_land_bucket``: how many territories the **defender** owns — ``1`` / ``2`` / ``3`` / ``4``
  (``4`` = 4+ tiles). Elimination-oriented: small empire → low bucket (opposite of rank).
  From ``player_land_count_bucket`` in ``missions.py``. ``player_land_rank_bucket`` remains
  in ``missions.py`` but is not used in the attack key.

Computed from the board *before* combat using ``continent_missing_for_territory`` in
``missions.py``, which delegates to the existing ``_continent_missing`` helper.

Old 4-field JSON history keys are back-compat padded with ``(1, 1, 4)`` on load; 6-field keys
with ``(4,)`` for missing defender land bucket (interpreted as 4+ lands).
Existing ``data/mctsland_history*.json`` files need a fresh self-play run to align with the
new 7-field keys.

=============================================================================

**Nested history JSON (attack + spree + deploy + fortify)**

```json
{
  "attack": { "(3,2,2,0,1,2,4)": { "visits": 10, "wins": 3 } },
  "spree": { "(1,0,1,2,1)": { "visits": 5, "wins": 2 } },
  "deploy": { "(8,3)": { "visits": 8, "wins": 2 } },
  "fortify": { "(1,0,2,4,1)": { "visits": 6, "wins": 1 } }
}
```

Legacy flat attack-only files load into ``attack`` with empty ``spree`` / ``deploy`` / ``fortify``.
Legacy ``placement`` section is ignored on load.
Old archives: ``data/attack_only/``.

**Spree state key** (post-conquest continue, 5-tuple)

``(is_mission, is_card, att_cont_bucket, def_land_bucket, ucb_rank)`` — logged when spree MCTS
chooses Continue. ``ucb_rank``: attack bandit score vs 1st-combat anchor (0 = &lt;50%, 1 = mid, 2 = ≥ anchor).
Replaces declining-% chain gate.

**Deploy state key** (2-tuple, max 50)

``(fortify_decile, att_units)`` — ``fortify_decile`` 1..10 from **this turn's** DEPLOY arms ranked by
fortify-table UCB1 (per-turn, not global); ``att_units`` = ``min(units[t], 5)``.

**Fortify state key** (6-tuple, post-strip — no ``att_units``)

``(def_neighbor_max, is_mission, is_card, att_cont, connectivity_all, connectivity_mission)``

- ``connectivity_all``: **other** own tiles in same component (0..5); alone tile → 0
- ``connectivity_mission``: mission-relevant tiles in cluster (0..4)
- Fortify skips isolated components via ``len(cluster) >= 2`` (equivalent to connectivity_all ≥ 1)