# Product Context

- Purpose: Fast, dramatic Risk-like with comeback mechanics and readable outcomes.
- Experience Goals:
  - Clear turn order; no bot actions during human turns.
  - Overruns feel powerful but fair; no phase interleaving.
  - Minimal UI friction; combat results visible with light FX.
  - Short **event log** under the mission strip: latest events at top; conquest combat line should read as older than same-tick “continent captured” / elimination lines (causal order in the vertical list).
  - Elimination objective: short on-screen line; full rules on hover (custom tooltip). Deploy mistakes: undo last manual bonus placement when allowed.
  - Per-phase countdown that is easy to read (numeric label) and *feel* (compass rotation) without being distracting.
- Constraints:
  - Minimal refactors; keep file structure stable.
  - Server is single source of truth for all game state.
  - Multiplayer lobby is a separate scene; game scene loads only on Start Game.
  - Optional **Python** rollout sim (`mcts_train/`) is for offline tooling only; shipped game stays GDScript-only. **Mctsland** learns attack, spree (chain), and placement decisions from nested JSON history. Fresh entropy per ``new_game`` (no baked-in replay unless you add seeds later).