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
  - **MctslandBotPlayer**: REINFORCE = Rookie top-3 cascade; **DEPLOY** = one-shot fortify-decile + deploy 2-tuple UCB distribute (default softmax); **FORTIFY** = bulk strip + one-shot 6-tuple UCB distribute; ATTACK = **attack MCTS** + **spree MCTS**. ``--mcts-bandit-only`` / ``iterations=0`` = UCB1 bandit per table. ``--placement-distribute linear|softmax``, ``--placement-softmax-temp``.
  - **Nested history JSON**: ``{ "attack": {...}, "spree": {...}, "deploy": {...}, "fortify": {...} }``; legacy ``placement`` and 7-field deploy keys ignored on load.
  - **MCTS entrypoints** (`mcts_search.py`): ``run_mcts_attack``, ``run_mcts_spree`` (placement MCTS removed from bot path).
  - **CLI** (selfplay / smoke / calibrate): ``--mcts-iterations``, ``--mcts-depth``, ``--mcts-breadth``, ``--mcts-rollout``, ``--mcts-no-history-prior``, ``--mcts-bandit-only``, ``--mcts-history``, ``--placement-distribute``, ``--placement-softmax-temp``, **``--workers``**, ``--batch-size``, ``--save-every`` / ``--progress-every``.
  - **`mcts_selfplay.py`**: default ``--full-attack`` (spree requires ``combat_one_round_only=False``); **`rollout_limits.py`** dynamic micro-step cap; **`smoke_rollout.py`** failure dumps to ``logs/``.
  - **`mcts_calibrate.py`**, **`mcts_search_smoke.py`**.
  - **``.gitignore``**: ``__pycache__/``, ``*.py[cod]``; bytecode untracked from repo.
  - **Parallel execution**: ``--workers W`` (0=all CPUs); ``--batch-size`` per task (default 1); selfplay saves at ``--save-every`` milestones; calibrate checkpoints ``data/mcts_calibration.json`` at ``--progress-every``.
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
- **Mctsland**: JSON backprop is still **match win**, not per-decision outcome. Attack MCTS still slow at default 100 iters; DEPLOY/FORTIFY are fast (one-shot bandit). Retrain after key schema changes (``placement`` → ``deploy``/``fortify``; deploy 7-tuple → 2-tuple). Legacy flat history and ``data/attack_only/`` lack spree/deploy/fortify stats.
