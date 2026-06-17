---
name: mcts-search
description: MCTS search and training patterns for animal-times-mcts — ephemeral trees, UCB1, rollouts, history JSON, parallelism. Load when working on mcts_search.py, MctslandBotPlayer attack logic, or training scripts.
---

# MCTS Search & Training

## Core API

```python
from mcts_train.mcts_search import run_mcts_attack

best_combat = run_mcts_attack(
    sim, state, root_seat,
    iterations=100,
    max_depth=5,
    max_breadth=5,
    rollout_kind="rookie",  # or "uniform"
    history=None,           # optional JSON priors dict
    ucb_c=math.sqrt(2.0),
)
```

Called by `MctslandBotPlayer` at ATTACK when `mcts_iterations > 0`.

## Ephemeral Tree Design

- **One fresh tree per combat choice** — not a persistent transposition table
- Root children = legal combats (`legal_root_combats`)
- Deeper nodes expand `Simulator.legal_actions` up to `max_breadth` (UCB1-ranked candidates)
- Each `MctsNode` holds a full `GameState` snapshot (not coarse attack keys)

## Algorithm Flow

1. **Select**: walk down via UCB1 best child until reaching expandable or terminal node
2. **Expand**: add up to `max_breadth` untried children from ranked legal actions
3. **Rollout**: apply actions up to `max_depth` steps; if not terminal → `_eval_truncated`
4. **Backprop**: propagate `z` up the path; `z=1` iff terminal winner == `root_seat`, else `z=0` (or heuristic for truncated)

## Defaults (match CLI)

| Param | Default | CLI flag |
|-------|---------|----------|
| iterations | 100 | `--mcts-iterations` |
| rollout depth | 5 | `--mcts-depth` |
| breadth | 5 | `--mcts-breadth` |
| rollout policy | rookie | `--mcts-rollout uniform\|rookie` |

## Truncated Evaluation

`_eval_truncated(sim, state, root_seat)` when rollout hits depth cap:

- `TERR_SCORE_CAP = 0.25` — territory count vs avg opponent
- `MISSION_SCORE_CAP = 0.25` — mission-type-specific progress
- Combined max **0.5** (not a win signal; fractional backup value)

## History JSON Priors

- Training produces `data/mctsland_history_*.json`
- Format: `{"(att,def,mission,coin,...)": {"visits": N, "wins": W}}`
- Root combat expansion can use visit/win ratio as prior (disable with `--mcts-no-history-prior`)
- Inference: `MctslandBotPlayer.from_history_file(path, history_readonly=True)`

### Known limitation
`notify_game_over` backprops **whole-game win** to all logged attack keys — not per-combat outcome.

## Bandit-Only Fallback

`--mcts-bandit-only` or `mcts_iterations=0`:

- No tree search; legacy UCB1 on coarse attack keys from global JSON table
- Fast table refresh for training data collection

## Parallelism

`--workers W` (0 = all CPUs) in `mcts_selfplay.py` and `mcts_calibrate.py`:

- `multiprocessing.Pool` + `imap_unordered`; pool initializer loads Simulator/history once per worker
- `--batch-size N` (default 1): matches per parallel task — decoupled from progress/save cadence
- Selfplay: `--save-every` flushes JSON in serial mode only; parallel mode saves after each returned task
- Calibrate: `--progress-every K` prints and checkpoints every K completed matches to `data/mcts_calibration.json` (resume on re-run; `--fresh` to reset)

## Performance Tips

- Default 100 iters × many attacks per game = slow
- Tune: lower `--mcts-iterations`, `--mcts-depth`, `--mcts-breadth`
- Fast training pass: `--mcts-bandit-only` then refine with MCTS
- Parallel: `--workers 8` for game-level parallelism

## Smoke / Verify

```bash
python3 scripts/mcts_search_smoke.py
python3 scripts/smoke_rollout.py --bots 2 --mcts-iterations 20
python3 scripts/mcts_selfplay.py --bots 4 --matches 10 --mcts-iterations 20 --workers 4
```

## Files to Touch Together

| Change | Files |
|--------|-------|
| Search algorithm | `mcts_train/mcts_search.py` |
| Attack key / logging | `mcts_train/players/mctsland_bot_player.py` |
| Rollout legality | `mcts_train/simulator.py` |
| CLI flags | `scripts/mcts_selfplay.py`, `scripts/smoke_rollout.py`, `scripts/mcts_calibrate.py` |
| Heuristic | `mcts_train/mcts_search.py` (`_eval_truncated`, mission score helpers) |

Load this skill when modifying search, training loops, or Mctsland ATTACK behavior.
