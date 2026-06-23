---
name: game-domain
description: Animal Times / Milos rules domain — phases, combat, missions, elimination, Attack of Despair, and board state for MCTS. Load when working on simulator rules, missions, or bot policy.
---

# Game Domain (Animal Times / Milos Rules)

Standalone Python simulator for a Risk-like strategy game with custom **Milos combat rules**.

## Turn Structure

```
REINFORCE → ATTACK → DEPLOY → FORTIFY → (next player) → GAME_OVER
```

- **REINFORCE**: place starting armies for the turn
- **ATTACK**: combat against adjacent enemy territories (chain attacks / overruns possible)
- **DEPLOY**: spend bonus armies from continent captures / cards
- **FORTIFY**: move armies between connected friendly territories (single-hop adjacency)
- Movement is **direct neighbors only** (from `territory_connections.json`)

## Combat (Milos Rules)

Differs from standard Risk:

1. **Subtraction damage**: `attacker_die - defender_die` → losses (capped so units never go negative)
2. **Mutual destruction**: both sides at 0 → recursive reroll ("REROLL!!!")
3. **Bidirectional conquest**: if attacker eliminated, defender may capture attacker's territory
4. **Overrun**: conquer without losing attacker units → can chain further attacks in ATTACK phase
5. **Attack of Despair (AoD)**: when player's max units ≤ 1; special desperate rules; anti-chain prevents exploitation after conquest

Conquest triggers when units reach **exactly 0**, not below.

## Missions

Loaded from `gamedata/missions.json`. Types include:

- **Conquest**: own all territories in required continent(s)
- **Elimination**: eliminate a target player (animal)
- **Special**: sLands, sTriple, any_third variants

`MissionSpec` in `missions.py` drives win checks and mission-value buckets for Mctsland attack keys.

### Mission Buckets (for attack key / heuristics)
`mission_territory_values()` on defender tile:
- `0` = not mission-focused
- `1` = flexible (~0.5)
- `2` = priority (~1.0)

## Elimination

- Player with 0 territories eliminated
- Removed from `player_queue`; turn advances if they were current
- Eliminator gets bonus units; elimination missions may retarget
- One player left → `last_standing` victory

## Board State for Decisions

Static layers (see `state_features.md`):

| Layer | Source |
|-------|--------|
| Adjacency | `gamedata/Territories/territory_connections.json` |
| Units per land | `GameState` unit array |
| Owners | `GameState` owner array |
| Mission lands | derived from mission spec |
| Card/coin lands | hand + territory card positions |

Derivable at runtime (don't duplicate in features unless perf-critical):

- Current phase → from `GameState.phase`
- Legal actions → `Simulator.legal_actions()`
- Continent control → from owners + adjacency

## Attack State Key (Mctsland history / bandit)

Sparse key per combat candidate — **not** MCTS tree node identity:

```
(att_units, def_units, mission_bucket, coin_kind,
 att_cont_bucket, def_cont_bucket, def_land_bucket)
```

- `att_units`: capped at 5 — attacking tile + spare from connected own cluster
- `coin_kind`: 0=none, 1=saber, 2=gun, 3=cannon (max matching token on defender tile)
- Continent buckets: tiles still needed to fully own defender's continent (1/2/3+)
- `def_land_bucket`: defender territory count bucket (1/2/3/4+)

Old JSON keys padded on load for back-compat; retrain after schema changes.

## Truncated MCTS Eval (`_eval_truncated`)

When rollout hits depth cap:

- **Territory ratio** (max 0.25): my lands vs average opponent
- **Mission progress** (max 0.25): conquest % / elimination target lands / special mission metrics
- Total heuristic capped at **0.5**

## Bot Behavior Summary

| Bot | Non-attack | ATTACK |
|-----|------------|--------|
| Rookie | standard heuristics | looped attack + overrun slide |
| Mctsland | one-shot UCB placement (DEPLOY/FORTIFY); Rookie REINFORCE cascade | MCTS (`run_mcts_attack`) or legacy UCB1 bandit |

**Mctsland deploy / fortify:** DEPLOY uses 2-tuple deploy keys `(fortify_decile, att_units)` — decile ranks this turn's dests by fortify-table UCB1; fortify keeps 6-tuple keys. One-shot distribute via `--placement-distribute` (default softmax).

Mctsland chain attacks: UCB1 quality gate vs first combat anchor (90%→50% floor); no fixed chain cap like Rookie's 3.

Load this skill when modifying `simulator.py`, `missions.py`, combat logic, or bot attack policy.
