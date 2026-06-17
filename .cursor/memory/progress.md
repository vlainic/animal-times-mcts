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
  - **MctslandBotPlayer**: REINFORCE = Rookie top-3 cascade consolidate; DEPLOY/FORTIFY = **placement MCTS** with **session cache** (init all dest keys once; pick 1 = MCTS, 2+ = bandit; refresh only changed tiles); FORTIFY = strip-then-place per cluster; ATTACK = **attack MCTS** + **spree MCTS** (stop + continue both logged). ``--mcts-bandit-only`` / ``iterations=0`` = UCB1 bandit per table.
  - **Nested history JSON**: ``{ "attack": {...}, "spree": {...}, "placement": {...} }``; ``load_history_from_json`` / ``save_history_to_json``; ``ensure_history_bundle`` for shared training dict; worker merge via in-place ``merge_history_tables`` + reassignment.
  - **MCTS entrypoints** (`mcts_search.py`): ``run_mcts_attack``, ``run_mcts_spree``, ``run_mcts_placement``.
  - **CLI** (selfplay / smoke / calibrate): ``--mcts-iterations``, ``--mcts-depth``, ``--mcts-breadth``, ``--mcts-rollout``, ``--mcts-no-history-prior``, ``--mcts-bandit-only``, ``--mcts-history``, **``--workers``** (parallel game-level processing).
  - **`mcts_selfplay.py`**: default ``--full-attack`` (spree requires ``combat_one_round_only=False``); **`rollout_limits.py`** dynamic micro-step cap; **`smoke_rollout.py`** failure dumps to ``logs/``.
  - **`mcts_calibrate.py`**, **`mcts_search_smoke.py`**.
  - **``.gitignore``**: ``__pycache__/``, ``*.py[cod]``; bytecode untracked from repo.
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
- **Mctsland**: JSON backprop is still **match win**, not per-decision outcome. Full games with default 100×MCTS per attack/spree/placement step are **slow** — placement cache skips repeated placement MCTS after first pick per session; tune depth/breadth/iterations, ``--mcts-bandit-only``, or ``--workers``. **Self-play must use full-attack** (default) or spree table stays empty. Retrain after key schema changes. Legacy flat history and ``data/attack_only/`` lack spree/placement stats.
