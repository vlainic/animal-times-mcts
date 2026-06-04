# Active Context

## Current focus
- **Shipped game**: pure GDScript (no Python in export); territory nodes + MetaData; one-click Export All.
- **Optional offline**: `Python/mcts_train/` for Milos-rule simulator, **Mctsland** bot (real ephemeral MCTS at ATTACK + JSON history), and self-play training (not runtime).
- Recent HUD/MP polish: **DEPLOY undo**, **elimination mission compact display + custom tooltip**, optional **elimination-mission assign cheat**, **game event log** under MissionDisplay.

## Recent changes (session summary)
- **Python `mcts_train` (offline Milos sim, not shipped)** — training / smoke only; lives under `Python/mcts_train/`:
  - **`GameState` RNG (environment + policy)**: ``rng_cards`` (deck / reshuffle), ``rng_dice`` (combat + mutual-destruction rerolls), ``rng_policy`` (stochastic bots / policies). No single ``rng`` on state.
  - **`Simulator.new_game(num_players, player_names, *, mission_pool=...)`**: draws **OS entropy** via ``numpy.random.SeedSequence().spawn(5)`` → independent streams for **board setup**, **mission shuffle** (from ``missions.json`` pool), **cards**, **dice**, **policy**; no caller-supplied seeds. ``deepcopy`` preserves all three generators for MCTS branches.
  - **`GameState.event_log`**: :class:`EventLog` (enabled via ``Simulator(log_events=True)``; FIFO ``max_lines``). Tags: **`[COMBAT]`**, **`[CONTINENT]`**, **`[ELIM]`**, **`[WIN]`** with mission detail from ``MissionSpec.raw`` (``_mission_win_log_detail``).
  - **Elimination from combat** / **`resolve_combat_milos`** / logs: unchanged behavior from prior notes.
  - **MCTS search** (`Python/mcts_train/mcts_search.py`): **ephemeral tree per ATTACK combat choice** — ``run_mcts_attack`` on ``GameState.copy()`` + ``Simulator.apply``; root-aligned backup ``z=1`` iff terminal ``winner == root_seat``. Defaults: **100 iterations**, **depth 5**, **breadth 5** (see CLI below). Rollouts stop after ``--mcts-depth`` ``apply`` steps; **truncated → ``_eval_truncated`` heuristic** (territory ratio + mission progress, each capped at 0.25, total max 0.5). Per-node ``--mcts-breadth``: at most K child candidates, **UCB1-ranked** (root combats can use JSON **history** as priors when expanding). Tree nodes hold full state snapshots (not coarse attack keys). Deeper nodes expand ``legal_actions`` (DEPLOY can be wide; breadth cap limits ``untried``).
  - **Truncated eval** (`_eval_truncated`): 0.25 territory-ratio (my lands vs avg opponent) + 0.25 mission progress (conquest: % tiles owned in required continents; elimination: fewer target lands = better, -0.01 per land; sLands: my_terr/20; sTriple: avg top-3 continent %).
  - **Parallelism** (`--workers W`): Both `mcts_selfplay.py` and `mcts_calibrate.py` support `--workers` (default 1; 0 = all CPUs). Uses `multiprocessing.Pool` + `imap_unordered` for streaming results. **Selfplay**: `--save-every` controls sub-chunk size; history merged+saved after each sub-task completes. **Calibrate**: `--progress-every` controls sub-chunk size; progress printed per task completion.
  - **Smoke / calibrate**: ``smoke_rollout.py``, ``mcts_calibrate.py`` share ``run_one_rollout``; ``mcts_search_smoke.py`` quick legality check.
  - **Smoke script** `Python/mcts_train/scripts/smoke_rollout.py`: **``--bots``** pattern (``1``=Rookie, ``2``=Mctsland). MCTS CLI (Mctsland): ``--mcts-iterations`` (default 100), ``--mcts-depth`` (5), ``--mcts-breadth`` (5), ``--mcts-rollout`` ``uniform|rookie``, ``--mcts-no-history-prior``, ``--mcts-bandit-only`` (``iterations=0`` → legacy JSON bandit only). ``--mcts-history PATH`` = inference read-only.
  - **Mctsland bot** (`Python/mcts_train/players/mctsland_bot_player.py`):
    - **REINFORCE / DEPLOY / FORTIFY** = **Rookie** delegate.
    - **ATTACK**: overrun slide via Rookie; then **``run_mcts_attack``** when ``mcts_iterations > 0``; else legacy **UCB1 bandit** on same sparse keys.
    - **Coarse state key** (JSON history + bandit fallback): ``(att_units, def_units, mission_bucket, coin_kind)`` — **not** used as MCTS node identity (only priors / post-game table).
    - **Training**: ``notify_game_over`` still increments JSON ``visits``/``wins`` per logged attack key (whole-game win).
    - **Inference**: ``from_history_file``, ``history_readonly=True``.
  - **Self-play** `mcts_selfplay.py`: same MCTS CLI flags; ``mission_pool="all"``; ``data/mctsland_history_<stamp>.json``; ``--history``, ``--save-every``.
  - **Simulator elimination fix**: ``apply_player_elimination`` → ``_remove_player_from_turn_queue`` (Godot parity): eliminated seat removed from ``player_queue``; if they were current → next seat, **REINFORCE** (no DEPLOY with 0 tiles). One seat left → ``last_standing`` win. Rookie ``_deploy`` + DEPLOY ``legal_actions`` guard for empty ownership.
  - **`.gitignore`**: ``Python/mcts_train/logs/`` and ``Python/mcts_train/data/`` (training JSON not committed).
  - **Logs folder**: `Python/mcts_train/logs/` for user-generated smoke dumps.
- **Game event log** (`HUD/event_log.gd` + `event_log.tscn`, child of `MultiplayerScene`): Newest-at-top (`push_front` per row), ~10 lines, Pirata One body font, BBCode tinting from `Globals.get_active_mp_manager().players[peer_id]["color"]`; territory segments use defender peer id before conquest where relevant. **Server**: `_evt_seg`, `_push_game_event_segments`, `_push_secondary_or_immediate_segments` + `_secondary_game_events_buffer_for_combat` while `_buffer_secondary_game_events_for_combat_resolve` is true during `_resolve_combat_on_server` (continent capture line, elimination, etc. buffered). **Flush order fix**: after building `combat_segments`, **push combat first**, then loop buffered secondaries and push each — because the log uses `push_front`, **major events end up above the combat line** (newest first: continent / elimination reads as happening after the conquest combat). **Layout**: `_update_avatar_scale()` in `multiplayer_scene.gd` places log under mission; strip width **80% of mission width** (`mission_width * 0.8`). **RPC**: `rpc_push_game_event_segments` / `rpc_push_game_event_line` on `multiplayer_scene.gd`; log cleared at game start. Godot 4: RichTextLabel uses **`text`**, not `bbcode_text`.
- **DEPLOY UNDO**: Server `human_deploy_undo_stack` (LIFO territory names) for **manual** human deploys only; `_deploy_army_to_territory_direct(..., record_for_undo_stack)` with `false` for `_auto_deploy_for_player`. `request_undo_last_deploy` + `_undo_last_deploy_for_peer`; stack cleared on phase transitions and `auto_advance_deploy`. `MultiplayerScene.deploy_undo_stack_depth_by_peer` + `rpc_sync_deploy_undo_stack_depth`; `TurnControls` Deploy button becomes **UNDO** when undo depth > 0 and local pending > 0 (no undo after final placement that advances to FORTIFY). `rpc_sync_pending_armies` also syncs **LocalPlayer** pending for the matching peer and refreshes phase buttons.
- **Elimination mission UI**: `mission_display` shows one line **Eliminate {Animal}.**; full rules moved to **`HUD/tooltip.tscn`** via **`MissionTooltipAnchor`** overlay; BBCode title **Eliminate {Animal}:** + body word-wrapped (~6 words/line) from description remainder (text after first `". "`). `tooltip.gd` branch **`MissionTooltipAnchor`** reads meta **`tooltip_bbcode`**.
- **Dev cheat**: `Globals.CHEAT_ALWAYS_ELIMINATION_MISSION` — when true, server `_assign_missions_to_players` prefers valid **elimination** missions for **human** players (tutorial human mission unchanged). Toggle in `globals.gd`; turn off for release builds.

## Next steps
- Optional: wire **Mctsland** into Godot bot seat (load trained JSON + MCTS knobs or bandit-only for speed).
- Optional: heuristic at truncated rollout depth; progressive re-ranking of ``untried``; history priors on non-root actions.
- Optional: per-combat backprop into JSON (today: whole-game win on table only).
- Optional: re-apply **multi-land movement** in GDScript using **`docs/networkx_overhaul/networkx_revert.md`** (BFS in map, `is_adjacent` in territories, server connected-set check).
- Optional: re-apply **tutorial player-queue** (no shuffle) from same doc if desired.
- Optional: on elimination **retarget**, refresh mission `description` server-side so tooltip “If {animal}…” matches `target_animal` (known staleness if only `target_animal` updates).

## Active decisions
- No Python runtime in shipped game; bots and state stay in GDScript for simple export and no per-platform Python builds.
