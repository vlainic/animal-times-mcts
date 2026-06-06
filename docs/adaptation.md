# Standalone layout (`animal-times-mcts`)

Independent Python MCTS project (no Godot runtime).

## Layout

```text
animal-times-mcts/          # repo root
├── gamedata/               # map + missions JSON
├── data/                   # Mctsland history JSON
├── logs/                   # smoke / debug logs
├── scripts/                # CLI entrypoints (run from repo root)
│   ├── _bootstrap.py
│   ├── mcts_selfplay.py
│   ├── smoke_rollout.py
│   └── ...
└── mcts_train/             # Python package (library only)
    ├── paths.py            # repo_root(), data_dir(), logs_dir()
    └── ...
```

## Run from repo root

```bash
pip install -r requirements.txt
python3 scripts/smoke_rollout.py --bots 4
python3 scripts/mcts_selfplay.py --bots 4 --matches 10
python3 scripts/mcts_selfplay.py --mcts-iterations 20 --matches 1000 --workers 7
```

Do **not** use old paths like `mcts_train/scripts/...` or `Python/mcts_train/...`.

## Path helpers

- [`mcts_train/paths.py`](../mcts_train/paths.py): `repo_root()`, `data_dir()`, `logs_dir()`
- Scripts call `scripts/_bootstrap.setup()` so `import mcts_train` works without installing the package.

## Data

| Resource | Path |
|----------|------|
| Territories | `gamedata/Territories/*.json` |
| Missions | `gamedata/missions.json` |
| History | `data/*.json` |
| Logs | `logs/` |

## History JSON

Re-run self-play after attack-state key changes; old 4-field keys load with `(1,1)` padding but stats will not align with new 6-field keys.
