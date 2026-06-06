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

**Attack state key — defender land rank (added)**

- ``def_rank_bucket``: competition rank of the **defender** by owned territory count among living
  players — ``1`` = most lands, ``2`` / ``3`` = next tiers, ``4`` = rank 4+ (ties share a rank,
  e.g. ``1,2,2,4``). From ``player_land_rank_bucket`` in ``missions.py``.

Computed from the board *before* combat using ``continent_missing_for_territory`` in
``missions.py``, which delegates to the existing ``_continent_missing`` helper.

Old 4-field JSON history keys are back-compat padded with ``(1, 1, 4)`` on load; 6-field keys
with ``(4,)`` for missing defender rank.
Existing ``data/mctsland_history*.json`` files need a fresh self-play run to align with the
new 7-field keys.