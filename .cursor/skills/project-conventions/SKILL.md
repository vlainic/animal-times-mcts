---
name: project-conventions
description: Core conventions for the animal-times-mcts Python package — layout, simulator SSOT, bots, scripts, and data paths. Load before generating or refactoring mcts_train code.
---

# Project Conventions (mcts_train)

## Repo Layout

```text
animal-times-mcts/          # repo root
├── gamedata/               # map + missions JSON (read-only)
├── data/                   # Mctsland history JSON (gitignored)
├── logs/                   # smoke / debug logs (gitignored)
├── scripts/                # CLI entrypoints — run from repo root
│   ├── _bootstrap.py       # sys.path setup for `import mcts_train`
│   ├── smoke_rollout.py
│   ├── mcts_selfplay.py
│   ├── mcts_calibrate.py
│   └── mcts_search_smoke.py
└── mcts_train/             # Python package (library)
    ├── paths.py            # repo_root(), data_dir(), logs_dir()
    ├── state.py            # GameState, GamePhase, EventLog
    ├── simulator.py        # Simulator, Action, Combat, apply()
    ├── map_data.py         # MapData from gamedata JSON
    ├── missions.py         # MissionSpec, mission checks, buckets
    ├── mcts_search.py      # run_mcts_attack, MctsNode, UCB1
    ├── features.py         # observation tensors for ML experiments
    ├── coins.py            # card/coin token logic
    └── players/
        ├── rookie_bot_player.py
        └── mctsland_bot_player.py
```

Do **not** use old paths like `mcts_train/scripts/...` or `Python/mcts_train/...`.

## Bootstrap & Imports

Scripts start with:

```python
from _bootstrap import setup
setup()
from mcts_train.simulator import Simulator
```

`mcts_train/paths.py` provides `ensure_repo_on_sys_path()` for library-internal use.

## Single Source of Truth

- **`GameState`**: board arrays, phase, turn queue, missions, RNG streams, event log
- **`Simulator`**: all state transitions — combat, deploy, fortify, elimination, phase advances
- **Bots** call `sim.legal_actions(state)` and return an `Action`; they never mutate state directly
- **MCTS** branches via `state.copy()` + `sim.apply(copy, action)`

Business logic for rules belongs in `simulator.py` / `missions.py`, not in CLI argparse blocks or bot classes (except policy/heuristics).

## Bot IDs (`--bots` pattern)

| ID | Player |
|----|--------|
| `1` | RookieBotPlayer |
| `2` | MctslandBotPlayer |
| `4` | (other bot types as defined in players/) |

Example: `--bots 1222` = seat 0 Rookie, seats 1–3 Mctsland.

## Data Paths

| Resource | Path |
|----------|------|
| Territories | `gamedata/Territories/*.json` |
| Missions | `gamedata/missions.json` |
| History JSON | `data/*.json` (via `paths.data_dir()`) |
| Logs | `logs/` (via `paths.logs_dir()`) |

Re-run self-play after attack-state key schema changes. Old keys load with padding but stats won't align with new semantics.

## Code Style

- Use type hints and `from __future__ import annotations`
- Prefer `pathlib.Path` over string paths
- Board state as `numpy` arrays on `GameState`
- Use `dataclass` / `IntEnum` where the codebase already does
- Keep functions focused; extract only when duplication is real
- Minimal diffs — match surrounding naming and structure

## Scripts — When to Use Which

- **`smoke_rollout.py`**: quick sanity check, mixed bots, optional event log dump
- **`mcts_selfplay.py`**: training — accumulates history JSON, `--save-every`, `--workers`
- **`mcts_calibrate.py`**: evaluation runs with streaming progress (`--progress-every`)
- **`mcts_search_smoke.py`**: fast legality / search plumbing check

All accept MCTS knobs: `--mcts-iterations`, `--mcts-depth`, `--mcts-breadth`, `--mcts-rollout`, `--mcts-history`, `--mcts-bandit-only`, `--mcts-no-history-prior`.

## Testing Approach

- No formal test suite required unless user asks
- Smoke scripts are the primary verification path
- When adding simulator rules: run `python3 scripts/smoke_rollout.py --bots 4` from repo root
- For MCTS changes: `python3 scripts/mcts_search_smoke.py`

Load this skill before generating or refactoring `mcts_train` or `scripts/` code.
