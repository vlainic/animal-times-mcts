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