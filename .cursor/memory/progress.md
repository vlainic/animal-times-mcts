# Progress

## What works
- Export (Linux/Windows) **without Python**; one-click Export All.
- Combat, Attack of Despair, multiplayer, bots — all **GDScript only** (territory nodes, MetaData, map).
- Server authority, RPC sync, phase timer, elimination, card system, missions.
  - HandDisplay card layout: card slots (hand and `CardsAwarded`) derive their size from placeholder Panels in `hand_display.tscn`, while coin art size is controlled separately via `Cards/treasure.tscn` and `hand_display.gd`.
- **DEPLOY (human)**: Manual bonus placements can be **undone** while pending armies remain and the player has at least one manual placement on the stack; Deploy HUD button shows **UNDO**. Timer auto-deploy does not push undo stack. Final placement that empties pending still auto-advances to FORTIFY (no undo after that).
- **Elimination mission HUD**: Short on-screen line **Eliminate {Animal}.**; long copy in **`HUD/tooltip.tscn`** with title **Eliminate {Animal}:** and word-wrapped body (`HUD/mission_display.gd` + `MissionTooltipAnchor` in `mission_display.tscn`).
- **Dev cheat** (optional): `Globals.CHEAT_ALWAYS_ELIMINATION_MISSION` forces elimination-style mission pick for humans in `_assign_missions_to_players` when valid missions exist in pool.
- **Game event log**: Under MissionDisplay in multiplayer; server-authoritative lines (combat summary + buffered continent / elimination / similar majors in one combat tick). **Order**: combat row pushed to log **before** buffered secondaries so **newest-at-top** shows continent/elimination **above** the combat that caused them (`server.gd` flush after `_resolve_combat_on_server`). Strip width 80% of mission panel.
- **Python `mcts_train`**: Offline Milos simulator + **Mctsland**; **not** shipped in export.
  - **Simulator**: split RNG streams; ``mission_pool="all"``; elimination + turn-queue parity with Godot.
  - **`mcts_search.py`**: Real **ephemeral MCTS** at ATTACK — select/expand/rollout/backprop on ``Simulator``; defaults **100** iters, **depth 5** applies per rollout, **breadth 5** children/node (UCB1 candidate filter); root combats + optional JSON priors. **Truncated rollouts** use ``_eval_truncated`` heuristic (0.25 territory ratio + 0.25 mission progress) instead of flat 0.
  - **MctslandBotPlayer**: Rookie non-attack; ATTACK uses MCTS when ``mcts_iterations > 0``; ``--mcts-bandit-only`` / ``iterations=0`` = legacy UCB1 on sparse JSON ``{ "(att,def,mission,coin)": {visits, wins} }``.
  - **CLI** (selfplay / smoke / calibrate): ``--mcts-iterations``, ``--mcts-depth``, ``--mcts-breadth``, ``--mcts-rollout``, ``--mcts-no-history-prior``, ``--mcts-bandit-only``, ``--mcts-history``, **``--workers``** (parallel game-level processing).
  - **`mcts_selfplay.py`**, **`smoke_rollout.py`**, **`mcts_calibrate.py`**, **`mcts_search_smoke.py`**.
  - **Parallel execution**: ``--workers W`` (0=all CPUs) via ``multiprocessing.Pool`` + ``imap_unordered``; selfplay saves history after each sub-chunk; calibrate prints progress per task. Sub-chunk size controlled by ``--save-every`` (selfplay) / ``--progress-every`` (calibrate).
  - **`load_history_from_json`**, **`from_history_file`** for inference.

## What was reverted
- NetworkX/Python backend, PythonBridge autoload, TCP bridge, PyInstaller/freeze scripts.
- **Multi-land movement** (moving to any connected same-owner territory); re-apply instructions in **`docs/networkx_overhaul/networkx_revert.md`** (BFS + `is_adjacent` in GDScript).
- Tutorial player-queue no-shuffle; snippet in same doc.

## Current status
- Codebase is at pre–NetworkX state; movement is single-hop; export is self-contained.
- Mission/phase UX includes deploy undo + compact elimination tooltip path described above.

## Known issues
- If multi-land or tutorial queue behavior is wanted again, apply changes from `networkx_revert.md`.
- **Elimination retarget** (Godot HUD): server updates `target_animal` but not always long `description`; tooltip prose can lag retarget.
- **Mctsland**: JSON table backprop is still **match win**, not combat outcome. Full games with default 100×MCTS per attack are **slow** — tune depth/breadth/iterations, use ``--mcts-bandit-only`` for fast table refresh, or use ``--workers 8`` for parallel runs. Retrain JSON after ``att_units`` key formula changes.
