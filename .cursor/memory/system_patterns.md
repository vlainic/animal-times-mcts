# System Patterns: Animal Times Demo

## Architecture Overview
The game uses a **component-based architecture** with clear separation between visual presentation and game logic, now enhanced with **centralized server authority** and **structured game phases**.

## Key Design Patterns

### **NEW** Overrun Detection and Handling Patterns

### **NEW** Overrun Detection Pattern
**Pattern**: Combat result analysis for overrun situations
```gdscript
func is_overrun(_attacking_territory: String, defending_territory: String, attacker_peer_id: int, attacker_units_before: int, attacker_units_after: int) -> bool:
    # Check if attacker units are the same before and after
    var units_unchanged = attacker_units_before == attacker_units_after
    
    # Check if defending territory is now owned by the attacker
    var territory_conquered = _is_territory_owned_by_player(defending_territory, attacker_peer_id)
    
    return units_unchanged and territory_conquered
```

### **NEW** Overrun Handling Pattern
**Pattern**: Automatic unit movement after successful overrun conquests
```gdscript
func handle_overrun(attacking_territory: String, defending_territory: String, _attacker_peer_id: int):
    # Get current unit count in attacking territory
    var current_units = _get_territory_unit_count(attacking_territory)
    
    # Calculate units to move (all except 1)
    var units_to_move = current_units - 1
    
    if units_to_move > 0:
        # Use the existing helper function to move units
        await _move_units_between_territories(attacking_territory, defending_territory, units_to_move)
```

### **NEW** Enhanced Bot Strategy Pattern
**Pattern**: Overrun mode prioritizes high-value attacks
```gdscript
func _calculate_weighted_attacks(overrun_mode: bool = false) -> Array:
    # Overrun mode: only select options with weight > 10
    if overrun_mode:
        attack_options = attack_options.filter(func(option): return option.weight > 10)
    else:
        # Normal tiered selection logic
        var filtered_options = attack_options.filter(func(option): return option.weight > 10)
        # ... rest of normal logic
```

### **NEW** Audio System Patterns

### **NEW** Background Music Management Pattern
**Pattern**: Context-aware music system with seamless transitions
```gdscript
enum MusicState { MENU_SPACE, GAME_SPACE, NONE }

func _detect_music_state(scene_name: String) -> MusicState:
    match scene_name:
        "MainMenu", "RulesScene":
            return MusicState.MENU_SPACE
        "MultiplayerScene":
            if _is_game_active():
                return MusicState.GAME_SPACE
            else:
                return MusicState.MENU_SPACE
        _:
            return MusicState.NONE
```

### **NEW** Game State Detection Pattern
**Pattern**: Game is active when current scene is MultiplayerScene and map is visible (lobby is a separate scene; no lobby node in game scene).
```gdscript
func _is_game_active() -> bool:
    """Check if the game is actually active - MultiplayerScene with map visible (lobby is separate scene)."""
    var multiplayer_scene = get_tree().current_scene
    if multiplayer_scene and multiplayer_scene.name == "MultiplayerScene":
        var map = multiplayer_scene.get_node_or_null("Map")
        return map != null and map.visible
    return false
```

### **NEW** Lobby / Game Scene Separation Pattern
**Pattern**: Lobby and game are separate scenes for a clean lifecycle and no stale state.
- **Flow**: MainMenu → lobby scene (`steam_lobby.tscn` or `simple_lobby.tscn`) → on Start Game → `change_scene_to_file("res://Multiplayer/multiplayer_scene.tscn")`; Back from lobby → reset MP state → `change_scene_to_file("res://main_menu.tscn")`.
- **MultiplayerScene**: No lobby in tree. In `_ready()` (non–single-player), if `multiplayer.has_multiplayer_peer()` and `Globals.get_active_mp_manager().players.size() >= 1` then `call_deferred("start_multiplayer_game")`; else defensive fallback to main menu.
- **Why**: Clean lifecycle; no shared lobby instance; Back/exit always resets state and avoids stale "1 players connected" and get_tree() null issues.

### **NEW** Post-Conquest Local + RPC Pattern

**Pattern**: Ensure post-conquest movement runs on single-player, host, and guests without duplicating logic

```gdscript
if start_post_conquest_mode and main_scene:
    # Always run locally on this peer (SP + host)
    main_scene.start_post_conquest_movement(attacking_territory, defending_territory)
    # In multiplayer, also broadcast to guests
    if not Globals.IS_SINGLE_PLAYER:
        main_scene.rpc_start_post_conquest_movement.rpc(attacking_territory.name, defending_territory.name)
```

**When to use**: You use an RPC with `call_remote` (e.g. `@rpc("authority", "call_remote", "reliable")`) for behavior that must run on all peers, including the server/host (e.g. starting post-conquest movement after a successful attack).

**Why**: `call_remote` RPCs do not execute on the calling peer. By calling the function once locally and then conditionally sending the RPC only in multiplayer, single-player and host get the behavior immediately, while guests receive it via RPC. This avoids duplicated `start_post_conquest_movement` lines and keeps the pattern identical for Steam and ENet.

**Where implemented**: `server.gd` ~1652–1659 in the `start_post_conquest_mode` block.

### **NEW** Music Transition Pattern
**Pattern**: Smooth music transitions with overlap for seamless experience
```gdscript
func _switch_to_game_music():
    # Fade out menu music
    if menu_music_player.playing:
        var tween = create_tween()
        tween.tween_property(menu_music_player, "volume_db", -80.0, 1.0)
        tween.tween_callback(menu_music_player.stop)
    
    # Start game music with overlap
    game_music_player.volume_db = default_volume_db
    game_music_player.play()
```

### **NEW** HandDisplay Card Sizing Pattern
**Pattern**: Use placeholder Panels in `hand_display.tscn` to derive card slot positions and base size, while keeping coin art sizing independent.
- The first placeholder Panel in `CardContainer`/`CardContainer2`/`CardContainer3` (e.g. `Card1`) provides `main_hand_size` in `hand_display.gd`, and all hand card positions come from those placeholders’ `global_position`.
- Changing `custom_minimum_size` / size of these placeholder Panels shrinks or grows the **card slots** (overall card footprint) but does **not** directly change the coin sprite size.
- Coin visuals come from `Cards/treasure.tscn` (`Sprite2D.scale`) and any overrides in `setup_card_for_display` in `hand_display.gd`; adjust those when you want coin-only scaling.

### **NEW** Attack Sound Integration Pattern
**Pattern**: Animal-specific combat audio with server synchronization
```gdscript
func _play_attack_sound(combat_result: Dictionary):
    """Play attack sound based on attacker's animal type"""
    var attacker_animal = combat_result.get("attacker_animal", "")
    if attacker_animal.is_empty():
        return
    
    var sound_path = "res://HUD/Avatars/attack_sounds/" + attacker_animal + ".ogg"
    if ResourceLoader.exists(sound_path):
        var audio_stream = load(sound_path)
        if audio_stream:
            attack_sound_player.stream = audio_stream
            attack_sound_player.play()
```

### **NEW** Audio File Format Conversion Pattern
**Pattern**: Convert unsupported formats to Godot-compatible formats
```gdscript
# Convert .wma files to .ogg for Godot compatibility
# Use ffmpeg: ffmpeg -i input.wma output.ogg
# Update code references from .wma to .ogg
```

## **NEW** Server Authority System
- **Centralized Control**: `server.gd` manages all critical game state and validates actions
- **Game Phases**: REINFORCE, ATTACK, DEPLOY, FORTIFY, GAME_OVER with automatic progression
- **Turn Management**: Advanced turn system with phase integration and validation
- **Signal System**: `phase_changed` and `turn_changed` signals for UI updates
- **Server Authority**: All critical game actions validated by server for multiplayer consistency
- **Bot Integration**: Enhanced with bot-specific logic for DEPLOY phase handling and army management

### **NEW** Game Phase System
- **Phase Progression**: Automatic advancement based on game actions
- **Attack Phase Logic**: Auto-advances after combat (except overrun scenarios)
- **Turn Integration**: Phases tied to turn system for proper game flow
- **Structured Gameplay**: Clear progression with strategic depth
- **UI Integration**: Phase changes trigger UI updates across all clients

### **NEW** Phase Timer & Compass System
- **Server-only timer**: `server.gd` owns `phase_timer`, `phase_seconds_per_phase`, `phase_seconds_remaining`; ticks every second and broadcasts via `rpc_sync_phase_timer(seconds_remaining)`.
- **Single reset helper**: `_reset_phase_timer_for_new_phase()` is the only place that refills the timer and pushes the full value to all peers (host via direct call + guests via RPC); now also reused when ATTACK combat result is **OVERRUN** so the phase stays ATTACK but the timer/compass behave like a fresh move.
- **Client UI ownership**: `multiplayer_scene.gd` drives the visible numeric counter and compass needle; server never touches compass tween state directly.
- **Visibility vs animation**: `_update_phase_timer_visibility_for_current_turn()` shows the label only for the current player; `rpc_sync_phase_timer()` always updates the text, but only the current player sees it and gets the smooth compass tween.
- **Hard reset invariants**: On phase change and on any full-timer refill (including OVERRUN), compass rotation and label are reset together to the full phase value so the needle never stays tilted across turns or moves.
 - **Timer freeze**: A `phase_timer_frozen` flag plus `phase_timer.stop()` freeze ticking while combat resolves and while DEPLOY bonus/card animations run. `_on_phase_timer_tick()` returns early when frozen; `_reset_phase_timer_for_new_phase()` clears the flag, refills, and restarts the timer so time only runs during “live” player decision windows.

### **NEW** Turn and Phase Flow System

#### Phase Sequence Flow
**Pattern**: Circular phase progression with turn rotation
```
REINFORCE → ATTACK → DEPLOY → FORTIFY → (next player) REINFORCE
```

**Phase Details**:
1. **REINFORCE**: Player receives bonus armies based on territories/continents
   - Manual advancement: Player clicks "END REINFORCE" button
   - Auto-advance: None (player must manually advance)
   - Special: Attack of Despair check triggers here

2. **ATTACK**: Player can attack adjacent enemy territories
   - Manual advancement: Player clicks "END ATTACK" button (only after at least one attack)
   - Auto-advance: None (player must manually advance)
   - Validation: `attack_performed_this_turn` must be true to advance
   - Special: Auto-advances to DEPLOY after combat (except overrun scenarios)

3. **DEPLOY**: Player deploys bonus armies from card trades
   - Manual advancement: None (auto-advances automatically)
   - Auto-advance: `auto_advance_deploy()` called after delay when no pending armies remain
   - Special: Bot deployment blocks auto-advance (`bot_deployment_in_progress` flag)
   - Card Trading: Mandatory trades handled here, then auto-advance

4. **FORTIFY**: Player can move armies between connected territories
   - Manual advancement: Player clicks "END FORTIFY" button
   - Auto-advance: None (player must manually advance)
   - Turn End: Advancing from FORTIFY starts next player's turn

#### ATTACK Timeout Pattern (Humans)
- **Mandatory-first-attack**: On ATTACK timeout for a human:
  - If `attack_performed_this_turn == false`, the server calls `_auto_attack_for_player(peer_id)` to perform a single random legal attack based on owned territories and their enemy neighbors. If no legal attack exists, it behaves like the player clicking END ATTACK.
  - Whether the attack came from the player or from `_auto_attack_for_player`, once at least one attack has occurred, an ATTACK timeout **always advances** to DEPLOY instead of refilling the timer.
- **Bots**: ATTACK timeouts for bots keep the older behavior and just reset the phase timer without forced attacks.

#### Human DEPLOY Auto-Deploy Pattern
- **Server-side mirror**: Human pending bonus armies are mirrored on the server in `human_pending_armies[peer_id]`, incremented when armies are awarded and decremented on each successful deploy (manual or auto).
- **Timeout behavior**:
  - On DEPLOY timeout for a human, `_auto_deploy_for_player(peer_id)`:
    - Picks one random owned territory and calls `_deploy_army_to_territory_direct(...)` to place 1 army.
    - Decrements `human_pending_armies[peer_id]` by 1.
    - Calls a `MultiplayerScene` RPC to run `LocalPlayer.add_pending_armies(-1)` on the affected client so the avatar’s pending counter matches the auto-deploy.
  - After auto-deploy, the server checks the mirror:
    - If `human_pending_armies[peer_id] > 0`, it resets the phase timer and **stays in DEPLOY**.
    - If `== 0`, it calls `auto_advance_deploy()` to auto-pass to FORTIFY.

#### `advance_phase()` Function Pattern
**Pattern**: Server-authoritative phase progression with validation and bot integration
```gdscript
func advance_phase(requesting_peer_id: int):
    # Validation checks
    - Must be server
    - Must not be during bot deployment (bot_deployment_in_progress)
    - Must not be during bot handler execution (bot_handler_in_progress)
    - Must be current player's turn (requesting_peer_id == current_player.peer_id)
    
    # Phase progression logic
    match current_phase:
        REINFORCE → ATTACK: Direct transition
        ATTACK → DEPLOY: Requires attack_performed_this_turn == true, calls _handle_deploy_phase()
        DEPLOY → FORTIFY: Direct transition
        FORTIFY → REINFORCE: 
            - Advances to next player (current_turn_index++)
            - Resets attack_performed_this_turn = false
            - Clears conquerors_this_turn
            - Resets ATTACK_OF_DESPAIR
            - Checks AoD for new player
    
    # State synchronization
    - sync_turn_state.rpc() → All clients receive state
    - sync_attack_status.rpc() → All clients receive attack status
    - sync_turn_state() → Server updates locally
    - sync_attack_status() → Server updates locally
    
    # Signal emission
    - turn_changed.emit() → Only when FORTIFY → REINFORCE (new turn)
    - phase_changed.emit() → Always when phase changes
    
    # Bot integration
    - await _call_bot_handler_if_needed() → Blocks until bot completes phase
    - If bot: call_deferred("advance_phase") → Auto-advance to next phase
```

#### `auto_advance_deploy()` Function Pattern
**Pattern**: Automatic DEPLOY → FORTIFY transition with bot deployment blocking
```gdscript
func auto_advance_deploy():
    # Validation checks
    - Must be server
    - Must not be during bot deployment (bot_deployment_in_progress)
    - Must be in DEPLOY phase (current_phase == DEPLOY)
    
    # Phase transition
    - Sets current_phase = FORTIFY
    - sync_turn_state.rpc() → Broadcasts to all clients
    - sync_attack_status.rpc() → Broadcasts attack status
    - sync_turn_state() → Server updates locally
    - sync_attack_status() → Server updates locally
    - phase_changed.emit() → Emits signal locally
    
    # Bot handler integration
    - await _call_bot_handler_if_needed() → Blocks until bot completes FORTIFY phase
```

**Called From**:
- `_handle_deploy_phase()`: After checking for mandatory trades, if no trades or trades complete
- `request_auto_advance_deploy()`: Client RPC request (rarely used)

#### Signal Emission Pattern
**Pattern**: Multiple emission points for network synchronization
```gdscript
# phase_changed signal emitted from:
1. advance_phase() → Line 475 (after phase change)
2. sync_turn_state() → Line 337 (when clients receive RPC)
3. sync_attack_status() → Line 346 (when attack status changes)
4. auto_advance_deploy() → Line 524 (after DEPLOY → FORTIFY)
5. _handle_deploy_phase() → Line 633 (for bot deployment)

# turn_changed signal emitted from:
1. advance_phase() → Line 473 (only when FORTIFY → REINFORCE)
2. sync_turn_state() → Line 336 (when clients receive RPC)
3. initialize_turns() → Line 318 (game start)
```

**Why Multiple Emissions**:
- Server emits locally (for host player)
- RPC functions emit on clients (for all connected players)
- Network synchronization requires both server and client emissions
- Result: 1 server emission + N client emissions = multiple signals for same phase change

#### Bot Handler Integration Pattern
**Pattern**: Async bot phase handling with blocking and auto-advance
```gdscript
func _call_bot_handler_if_needed():
    # Check if current player is bot
    if not _is_player_bot(current_player_id):
        return  # Human player, no bot handler needed
    
    # Set flag to prevent phase advancement during bot execution
    bot_handler_in_progress = true
    
    # Call appropriate bot handler based on phase
    match current_phase:
        REINFORCE: await bot.handle_reinforce_phase()
        ATTACK: await bot.handle_attack_phase()
        DEPLOY: await bot.handle_deploy_phase()
        FORTIFY: await bot.handle_fortify_phase()
    
    # Clear flag after completion
    bot_handler_in_progress = false
```

**Bot Auto-Advance Pattern**:
```gdscript
# After bot handler completes in advance_phase():
if _is_player_bot(current_player_id) and not bot_handler_in_progress:
    call_deferred("advance_phase", current_player_id)
    # Deferred call prevents deep recursion and allows cleanup
```

#### Bot Deployment Blocking Pattern
**Pattern**: Prevent phase advancement during bot army deployment
```gdscript
# Flag set in _handle_deploy_phase() when bot has pending armies
bot_deployment_in_progress = true
phase_changed.emit(GamePhase.DEPLOY)  # Trigger bot deployment
await bot_player.deployment_completed  # Wait for bot to finish
bot_deployment_in_progress = false

# Blocked in advance_phase():
if bot_deployment_in_progress:
    return  # Cannot advance phase during bot deployment
```

#### Turn System Integration Pattern
**Pattern**: Phase reset and player rotation at turn end
```gdscript
# When advancing from FORTIFY:
current_turn_index = (current_turn_index + 1) % player_queue.size()
current_phase = GamePhase.REINFORCE  # Reset to first phase
attack_performed_this_turn = false  # Reset attack flag
conquerors_this_turn.clear()  # Reset conquest tracking
Globals.ATTACK_OF_DESPAIR = false  # Reset AoD mode
check_attack_of_despair_during_reinforce()  # Check new player's situation
```

#### Network Synchronization Flow
**Pattern**: Server authority with client state sync
```
1. Server: advance_phase() called
2. Server: Updates current_phase, current_turn_index
3. Server: sync_turn_state.rpc() → Sends to all clients
4. Server: sync_turn_state() → Updates server locally
5. Clients: Receive RPC → Update local state → Emit signals
6. Server: Emits signals locally (phase_changed, turn_changed)
7. All: UI updates via signal connections
```

**Result**: Multiple signal emissions (1 server + N clients) for same phase change, but all synchronized to same state.

### **NEW** Attack of Despair System
- **Desperate Situation Detection**: Triggers when player has ≤1 unit per territory
- **Special Combat Rules**: Allows attacking with just 1 unit (normally requires 2+)
- **Anti-Chain Attack Logic**: Prevents multiple attacks in AoD mode for balance
- **Auto-Phase Advancement**: Automatically advances to ATTACK phase with popup
- **Integration**: Seamlessly works with existing Milos Rules combat system

### **NEW** Player Elimination System
- **Elimination Detection**: `_handle_player_elimination()` when player loses all territories
- **Turn System Integration**: Eliminated players removed from turn rotation automatically
- **Conquest Rewards**: Eliminating player receives bonus units for complete conquest
- **Network Synchronization**: Elimination state synced across all clients
- **Game Flow**: Proper game continuation after player elimination

### **NEW** Deck Management System
- **Balanced Deck Creation**: 37 territory cards + 2 treasures with balanced unit type distribution
- **Unit Type Distribution**: Balanced pirate/mount/cannon distribution across all territories
- **Continent Organization**: Cards organized by continent with random territory order
- **Depot System**: Discarded cards stored for reshuffling when deck empties
- **Card Infrastructure**: All 37 territories have pirate, mount, and cannon card variants

### **NEW** Post-Conquest Movement System
- **Advanced Movement Mechanics**: Continued attacks after successful conquests
- **Overrun Detection**: Attacker can continue moving if no units lost during conquest
- **Mode Management**: Post-conquest mode with territory selection preservation
- **User Confirmation**: Dialog system for finishing conquest movement
- **Strategic Depth**: Continued attack opportunities after successful conquests

### **NEW** Conquest Tracking System
- **Turn-Based Tracking**: `conquerors_this_turn` array tracks conquests per turn
- **Server Authority**: All conquest tracking handled server-side
- **Continent Bonus**: Automatic continent bonus checking after conquests
- **Bonus Integration**: Conquest tracking integrated with bonus unit calculations
- **Strategic Gameplay**: Proper bonus unit system for conquest-based strategy

### Territory System
- **Base Node**: `Area2D` for each territory with collision detection
- **Child Components**:
  - `CollisionPolygon2D` - Interactive area definition
  - `MainSprite` - Visual territory representation  
  - `UnitLabel` - Unit count display
  - `MetaData` - Game state storage

### Metadata Pattern
Each territory uses a dedicated **MetaData node** for clean data separation:
```gdscript
# Stored in MetaData node using Godot's built-in metadata system
metadata/owner_id = -1        # Player ownership (-1 = neutral)
metadata/unit_count = 0       # Military units in territory
metadata/neighbors = []       # Connected territories for movement
```

### Event-Driven Updates
- **No polling**: Unit labels update only when `set_unit_count()` is called
- **Immediate feedback**: Visual changes happen instantly when game state changes
- **Performance optimized**: No unnecessary frame-by-frame checks

### Territory Coloring System
- **Sprite Modulation**: Uses `sprite.modulate` to apply player colors
- **Color Source**: Player colors from `MultiplayerManager.players[owner_id].color`
- **Default State**: Gray modulation for unowned territories (`owner_id = -1`)
- **Network Synchronized**: Color updates trigger on both local and network changes
- **Multi-sprite**: Applies consistently to MainSprite and SelectedSprite

### **NEW** Multiplayer Synchronization Patterns

#### Animal Assignment Flow
**Pattern**: Request→Validate→Confirm with proper conflict resolution
```gdscript
# Client requests animal
_on_animal_selected() → set_local_player() → send_player_info.rpc_id(1, info)

# Server validates and responds  
validate_and_assign_animal() → confirm_animal_assignment.rpc_id(client, result)

# Client confirms and updates UI
confirm_animal_assignment() → assignment_confirmed.emit() → UI update
```

#### Lobby Reset on Host Leave (ENet + Steam)
**Pattern**: Single `reset_lobby_state(leave_remote)` in both managers; guest cleans up without crash
- **mp_manager.gd**: `peer` is null by default; `create_server()`/`join_server()` create a fresh `ENetMultiplayerPeer` each time. `reset_lobby_state()` sets `multiplayer.multiplayer_peer = null`, closes and nulls `peer`, clears `players` and resets `local_player_info`. When guest detects host (peer 1) disconnect, emit `connection_failed` and return immediately (no further disconnect handling).
- **steam_mp_manager.gd**: `reset_lobby_state(leave_remote)` calls `Steam.leaveLobby(lobby_id)` when applicable, nulls peer and multiplayer_peer, clears lobby_id, lobby_members, is_host, host_steam_id, players, local_player_info.
- **Lobby (simple_lobby / steam_lobby)**: On `connection_failed`, reset UI first, then `Manager.call_deferred("reset_lobby_state", true)` and `call_deferred("_populate_animal_selection")`. All player list/status/start-button and player_connected/disconnected handlers guard with `if not multiplayer.has_multiplayer_peer(): return` to avoid using dead connection.

#### Steam Lobby Creation (Option A)
**Pattern**: Steam callbacks connected at init; multiplayer peer signals when entering Steam MP
- **SteamMPManager._ready()**: Connect Steam signals (lobby_created, lobby_joined, lobby_match_list, p2p_session_request, lobby_chat_update) only when `SteamManager.is_steam_initialized`, so callbacks fire after `Steam.createLobby()` regardless of `USE_STEAM_MULTIPLAYER` at boot. Connect multiplayer peer signals only when `Globals.USE_STEAM_MULTIPLAYER` or skip and connect later.
- **ensure_multiplayer_signals_connected()**: Public method that connects peer_connected, peer_disconnected, connected_to_server, connection_failed; idempotent. Called from steam_lobby._ready() so when user opens Steam lobby, peer events are wired even if they weren’t at game start.

#### Display ID Synchronization
**Pattern**: Server authority with complete state broadcasting
```gdscript
# Server assigns IDs sequentially
_on_player_connected() → player_display_ids[peer_id] = next_display_id

# Server broadcasts complete mapping to ALL clients
sync_display_ids.rpc() → receive_display_ids() → display_ids_updated.emit()

# All clients receive identical mappings
player_display_ids = {1: 1, 2: 2, 3: 3, 4: 4}  # Consistent across all clients
```

#### Debug Logging Pattern
**Pattern**: Sectioned logging with clear state tracking
```gdscript
print("=== SECTION NAME DEBUG ===")
print("Current state: ", variable)
print("Action taken: ", action_description)  
print("Result: ", outcome)
```

### **NEW** Dynamic UI Sizing Pattern
**Pattern**: Content-driven responsive design
```gdscript
func _resize_container_for_players(player_count: int):
    var height = base_height + (item_height * player_count)
    offset_top = -height - margin
    # Apply sizing with optimization checks
```

### **NEW** Card Hand Display System Patterns

#### Multi-Container Layout Pattern
**Pattern**: Support for multiple card containers with unified positioning system
```gdscript
# Three-container system for 12 total card positions
@onready var card_container = $CardContainer      # Cards 1-5 (bottom row)
@onready var card_container2 = $CardContainer2    # Cards 6-9 (middle row)  
@onready var card_container3 = $CardContainer3    # Cards 10-12 (top row)

# Unified placeholder initialization
var placeholder_nodes = [
    $CardContainer/Card1, $CardContainer/Card2, $CardContainer/Card3, $CardContainer/Card4, $CardContainer/Card5,
    $CardContainer2/Card6, $CardContainer2/Card7, $CardContainer2/Card8, $CardContainer2/Card9,
    $CardContainer3/Card10, $CardContainer3/Card11, $CardContainer3/Card12
]
```

#### Hand Display Layout Integration Pattern
**Pattern**: Use hand_display's built-in layout system instead of manual calculation
```gdscript
func _calculate_improved_layout(hand_display, actual_card_count: int):
    # Use hand_display's complete layout system that handles all containers
    var layout_info = hand_display.get_layout_info()
    var max_cards = hand_display.get_max_cards()  # Returns 12 for 3-container system
    
    # Use the hand_display's calculated positions for all cards
    for i in range(max_cards):
        if i in layout_info:
            var card_info = layout_info[i]
            card_positions.append(card_info.position)
            card_sizes.append(card_info.size)
```

#### Container Readiness Validation Pattern
**Pattern**: Ensure all containers are properly initialized before use
```gdscript
func _is_hand_ready() -> bool:
    """Check if hand display is ready and has all card containers"""
    if card_container == null or card_container2 == null or card_container3 == null:
        return false
    return true
```

#### Responsive Card Spacing Pattern
**Pattern**: Dynamic spacing that adapts to screen size while maintaining minimum spacing
```gdscript
# Ensure minimum spacing regardless of screen size
var spacing = max(10, (available_width - total_card_width) / (num_cards + 1))

# Distribute cards evenly across available space
var start_x = container_rect.position.x + spacing
for i in range(num_cards):
    var x = start_x + (i * (card_width + spacing))
```

#### Hand Display Integration Pattern  
**Pattern**: Leverage existing UI systems instead of duplicating layout logic
```gdscript
# Replace manual position calculation with hand_display's built-in system
# OLD: Manual calculation for only first 5 cards
# NEW: Use hand_display.get_layout_info() for all 12 positions
# Result: Proper support for all containers and positions
```

### **NEW** Milos Rules Combat System Patterns

#### Subtraction-Based Damage Pattern
**Pattern**: Variable damage based on dice difference instead of binary win/lose
```gdscript
# Calculate damage as difference between dice
var difference = attacker_dice[i] - defender_dice[i]
if difference > 0:
    # Cap damage to prevent negative units
    var capped_losses = min(difference, max(0, remaining_units))
    defender_losses += capped_losses
elif difference == 0:
    # Tie = both sides lose 1 (Milos rules)
    attacker_losses += 1
    defender_losses += 1
else:
    # Defender wins - attacker loses difference amount
    attacker_losses += abs(difference)
```

#### Mutual Destruction Reroll Pattern
**Pattern**: Recursive combat resolution until decisive winner
```gdscript
# Check for mutual destruction - REROLL if both would die!
if attacker_final <= 0 and defender_final <= 0:
    print("=== MUTUAL DESTRUCTION - REROLLING! ===")
    _resolve_combat_on_server(attacking_territory, defending_territory)
    return  # Let recursion handle the reroll
```

#### Bidirectional Conquest Pattern
**Pattern**: Both attacker and defender can capture territories
```gdscript
# Check for territory conquest (both directions!)
if defender_territory.get_unit_count() == 0:
    # Normal conquest: attacker wins
    defender_territory.set_owner_id(attacker_territory.get_owner_id())
    defender_territory.set_unit_count(1)
elif attacker_territory.get_unit_count() == 0:
    # Counter-attack: defender captures attacker's territory!
    attacker_territory.set_owner_id(defender_territory.get_owner_id())
    attacker_territory.set_unit_count(1)
```

#### Code Deduplication Pattern
**Pattern**: Single source of truth with minimal refactoring
```gdscript
# Single player redirects to server logic (territories.gd)
else:
    # Single player - use same server logic for consistency
    var map_node = get_node("/root/MainScene/Map")
    if map_node and map_node.has_method("_resolve_combat_on_server"):
        map_node._resolve_combat_on_server(attacking_territory, defending_territory)
```

#### **UPDATED** Pure Damage Calculation Pattern  
**Pattern**: Allow full damage calculation with conquest detection handling negatives
```gdscript
# Pure damage calculation without per-dice capping
for i in range(comparisons):
    var difference = attacker_dice[i] - defender_dice[i]
    if difference > 0:
        defender_losses += difference  # Full damage, no capping
    elif difference == 0:
        attacker_losses += 1
        defender_losses += 1
    else:
        attacker_losses += abs(difference)  # Full damage, no capping

# Handle conquest detection with <= 0 check
if defending_territory.get_unit_count() <= 0:  # Was == 0, now <= 0
    # Territory conquered! Handles negative units properly
```

### **NEW** Combat Visual Display Patterns

#### Separated Element Architecture Pattern
**Pattern**: Independent Control containers for each combat display element to enable animations
```gdscript
# Scene structure: All elements as direct children of CombatDisplay
CombatDisplay (Control - root)
├── AttackerAvatarContainer (Control with anchors)
├── AttDice1Container (Control with anchors, contains TextureRect)
├── AttDice2Container (Control with anchors, contains TextureRect)
├── AttDice3Container (Control with anchors, contains TextureRect)
├── ResultLabelContainer (Control with anchors, contains Label)
├── DefDice1Container (Control with anchors, contains TextureRect)
├── DefDice2Container (Control with anchors, contains TextureRect)
├── DefDice3Container (Control with anchors, contains TextureRect)
├── DefenderAvatarContainer (Control with anchors)
├── FightCloudSprite (Sprite2D)
└── AttackSoundPlayer (AudioStreamPlayer)
```

#### Anchor-Based Positioning Pattern
**Pattern**: Use anchors instead of layout containers for independent element positioning
```gdscript
# Attacker elements: left side (anchor_left = 0.0)
# Defender elements: right side (anchor_left = 1.0, anchor_right = 1.0)
# ResultLabel: center (anchors_preset = 8)
# Dice: positioned vertically using anchors, centered horizontally in their respective sides
```

#### Dice Image System Pattern
**Pattern**: Visual dice textures instead of numeric text display
```gdscript
func _get_dice_texture(value: int) -> Texture2D:
    """Get dice texture for a given dice value (1-6)"""
    var texture_path = "res://HUD/Assets/dice_" + str(value) + ".png"
    if ResourceLoader.exists(texture_path):
        var texture = load(texture_path)
        if texture:
            return texture
    return null

func _display_dice(attacker_dice: Array, defender_dice: Array):
    """Display dice values using textures"""
    for i in range(att_dice_rects.size()):
        if i < attacker_dice.size():
            var dice_value = attacker_dice[i]
            var dice_texture = _get_dice_texture(dice_value)
            if dice_texture:
                att_dice_rects[i].texture = dice_texture
            att_dice_rects[i].visible = true
```

#### Dice Shuffle Animation Pattern
**Pattern**: Random dice texture shuffling during wait period before revealing actual values
```gdscript
# Shuffle timer setup
shuffle_timer = Timer.new()
shuffle_timer.wait_time = 0.1  # Shuffle every 100ms for rapid effect
shuffle_timer.one_shot = false  # Repeating
shuffle_timer.timeout.connect(_shuffle_dice_textures)

func _shuffle_dice_textures():
    """Randomly shuffle visible dice textures for animation effect"""
    # Shuffle attacker dice
    for i in range(att_dice_rects.size()):
        if i < visible_attacker_dice_count and att_dice_rects[i].visible:
            var random_value = randi_range(1, 6)
            var dice_texture = _get_dice_texture(random_value)
            if dice_texture:
                att_dice_rects[i].texture = dice_texture
    
    # Shuffle defender dice (same pattern)
```

#### Shuffle Lifecycle Pattern
**Pattern**: Start shuffle on question mark display, stop on actual dice reveal
```gdscript
func _show_question_marks(attacker_dice_count: int, defender_dice_count: int):
    """Show dice positions and start shuffle animation"""
    visible_attacker_dice_count = attacker_dice_count
    visible_defender_dice_count = defender_dice_count
    
    # Show dice with random initial textures
    # ... setup dice visibility ...
    
    # Start shuffle animation
    shuffle_timer.start()

func _display_dice(attacker_dice: Array, defender_dice: Array):
    """Display actual dice values and stop shuffle"""
    shuffle_timer.stop()  # Stop shuffle before showing real values
    # ... display actual dice textures ...
```

#### Result Type Classification Pattern
**Pattern**: Six distinct combat outcome types for clear feedback
```gdscript
func _determine_result_text(combat_result: Dictionary) -> String:
    var attacker_losses = combat_result.get("attacker_losses", 0)
    var defender_losses = combat_result.get("defender_losses", 0)
    var conquered = combat_result.get("conquered", false)
    
    # OVERRUN: Attacker doesn't lose any units, defender loses all
    if attacker_losses == 0 and conquered:
        return "OVERRUN"
    # CONQUEST: Defender loses all units (but attacker lost some)
    elif conquered:
        return "CONQUEST"
    # COUNTER-ATTACK: Attacker loses all units
    elif defender_conquered:
        return "COUNTER-ATTACK"
    # ... additional result types
```

#### Auto-Hide Timer Pattern
**Pattern**: Clean UI that disappears automatically after combat
```gdscript
# Setup auto-hide timer
hide_timer = Timer.new()
hide_timer.wait_time = 5.0
hide_timer.one_shot = true
hide_timer.timeout.connect(_on_hide_timer_timeout)

# Start timer when display shown
func show_combat_result(combat_result: Dictionary):
    # ... display logic ...
    visible = true
    hide_timer.start()  # Auto-hide after 5 seconds
```

#### Seamless Integration Pattern
**Pattern**: Visual layer added without disrupting existing combat logic
```gdscript
# Use existing combat result data
func sync_combat_result(combat_result: Dictionary):
    # ... existing combat logic ...
    
    # Show combat display with existing data
    var combat_display = main_scene.get_node_or_null("CombatDisplay")
    if combat_display:
        combat_display.show_combat_result(combat_result)
```

### **NEW** Attack of Despair System Patterns

#### Desperate Situation Detection Pattern
**Pattern**: Detect when player is in desperate situation requiring special rules
```gdscript
func check_attack_of_despair_during_reinforce():
    var current_player_id = get_current_player_peer_id()
    var max_units = get_player_max_units(current_player_id)
    
    if max_units <= 1:
        # TRUE Attack of Despair: 1 unit per territory or less
        Globals.ATTACK_OF_DESPAIR = true
        sync_attack_of_despair.rpc(true)
        _handle_attack_of_despair_mode(current_player_id)
    else:
        Globals.ATTACK_OF_DESPAIR = false
        sync_attack_of_despair.rpc(false)
```

#### Anti-Chain Attack Pattern
**Pattern**: Prevent multiple attacks in desperate situations for game balance
```gdscript
# *** ATTACK OF DESPAIR ANTI-CHAIN ATTACK CHECK ***
# If Attack of Despair mode and any conquest occurred, force DEPLOY phase
if Globals.ATTACK_OF_DESPAIR and (territory_conquered or defender_conquered):
    start_post_conquest_mode = false  # Prevent chaining
    current_phase = GamePhase.DEPLOY   # Force to deploy phase
    print("AoD: Conquest achieved - forcing DEPLOY phase (no chain attacks)")
```

#### AoD Combat Integration Pattern
**Pattern**: Allow 1-unit attacks only in desperate situations
```gdscript
# Validate combat requirements
if attacking_territory.get_unit_count() <= 1:
    if Globals.ATTACK_OF_DESPAIR:
        print("Server: Combat allowed: Attacker has 1 unit in Attack of Despair mode")
        pass  # Allow the attack
    else:
        print("Server: Combat failed: Attacker must have more than 1 unit")
        return
```

### **NEW** Continent Label System Patterns

#### Map Label Integration Pattern
**Pattern**: Add continent identification labels to map scene for clear geographic reference
```gdscript
# ContinentLabels parent node with high z-index
[node name="ContinentLabels" type="Node2D" parent="."]
z_index = 10

# Individual continent labels with positioning and styling
[node name="MudflatsLabel" type="Label" parent="ContinentLabels"]
text = "MUDFLATS"
theme_override_font_sizes/font_size = 48
```

#### User-Customized Positioning Pattern
**Pattern**: Allow user to manually position labels for optimal map integration
```gdscript
# User positions labels with specific coordinates and rotations
offset_left = 506.0
offset_top = 187.0
offset_right = 758.0
offset_bottom = 254.0
rotation = -0.698132  # Optional rotation for better fit
```

#### Visual Hierarchy Pattern
**Pattern**: Labels appear above map elements but below UI elements
```gdscript
# ContinentLabels: z_index = 10 (above map)
# UI elements: z_index = 50 (above labels)
# Map elements: z_index = -5 to 0 (below labels)
```

### **NEW** Sea Connection Drawing Patterns

#### Custom Drawing System Pattern
**Pattern**: Use Node2D._draw() for efficient line rendering between territories
```gdscript
extends Node2D

func _draw():
    # Draw all sea connections as dashed lines
    for connection in sea_connections:
        if connection.size() >= 2:
            var pos1 = get_territory_center(connection[0])
            var pos2 = get_territory_center(connection[1])
            
            if pos1 != Vector2.ZERO and pos2 != Vector2.ZERO:
                draw_dashed_line(pos1, pos2, Color.BLACK, 4, 10)
```

#### Territory Center Calculation Pattern
**Pattern**: Use collision polygon geometry for accurate territory positioning
```gdscript
func get_territory_center(territory_name: String) -> Vector2:
    var territory = map_node.find_territory(node_name)
    var territory_pos = territory.global_position
    
    # Use collision polygon center for accuracy
    var collision_node = territory.get_node_or_null("CollisionPolygon2D")
    if collision_node and collision_node.polygon.size() > 0:
        var center = Vector2.ZERO
        for point in collision_node.polygon:
            center += point
        center /= collision_node.polygon.size()
        territory_pos += center
    
    return to_local(territory_pos)  # Convert for drawing
```

#### Data-Driven Connection Pattern
**Pattern**: Load connection data from JSON for maintainable sea route definitions
```gdscript
func load_sea_connections():
    var file = FileAccess.open("res://Map/Territories/sea_connections.json", FileAccess.READ)
    var json_string = file.get_as_text()
    sea_connections = JSON.parse_string(json_string)
    queue_redraw()  # Trigger redraw after loading
```

#### Mission Display Positioning Pattern
**Pattern**: Handle script-based positioning conflicts in responsive UI systems
```gdscript
# Apply positioning with offset adjustment
offset_left = scaled_margin
offset_top = scaled_margin + scaled_size.y + scaled_margin * 0.5 + 100  # +100 offset
offset_right = scaled_margin + mission_width
offset_bottom = scaled_margin + scaled_size.y + scaled_margin * 0.5 + mission_height + 100
```

### **NEW** Bot Architecture Patterns

#### Inheritance-Based Bot System Pattern
**Pattern**: Clean separation of infrastructure vs behavior using Godot's inheritance system
```gdscript
# BotPlayer (Base Class) - Infrastructure Only
class_name BotPlayer extends Player

# Core infrastructure methods
func _load_territory_data(): pass
func _calculate_owned_territory_indexes(): pass
func _get_neighbors_by_index(territory_index: int): pass
func _get_territory_name_by_index(index: int): pass

# Phase signal handling
func _connect_phase_signals(): pass
func _on_phase_changed(phase): pass
func _handle_phase_deferred(phase): pass

# ChaoticBotPlayer (Specialized Class) - Behavior Only
class_name ChaoticBotPlayer extends BotPlayer

# Override phase handlers with chaotic-specific logic
func handle_reinforce_phase(): pass
func handle_attack_phase(): pass
func handle_deploy_phase(): pass
func handle_fortify_phase(): pass

# Chaotic-specific decision making
func _pick_random_attack() -> Dictionary: pass
```

### **NEW** BotPlayer Card Assignment & Bonus Army Patterns

#### BotPlayer Scene Instantiation Pattern
**Pattern**: Use scene instantiation instead of direct class instantiation for proper node structure
```gdscript
func _create_bot_player(bot_type: String) -> BotPlayer:
    # Load player scene to get the node structure (including Cards node)
    var player_scene = preload("res://Players/player.tscn")
    var bot_player = player_scene.instantiate()
    
    # Apply bot script after scene instantiation
    match bot_type:
        "Chaotic":
            var script = load("res://Players/Chaotic/chaotic_bot_player.gd")
            bot_player.set_script(script)
    
    return bot_player
```

#### Bot Army Awarding Pattern
**Pattern**: Server-side army awarding with metadata storage for bots
```gdscript
func _award_armies_to_player(peer_id: int, army_count: int):
    # Check if this is a bot player
    if _is_player_bot(peer_id):
        # Handle bot army awarding
        var bot_player = find_player_by_peer_id(peer_id)
        if bot_player:
            var current_bonus = bot_player.get_meta("pending_bonus_armies", 0)
            bot_player.set_meta("pending_bonus_armies", current_bonus + army_count)
        return
    
    # Handle human player army awarding (existing logic)
```

#### Bot Deploy Phase Pattern
**Pattern**: Automatic bonus army detection and deployment during deploy phase
```gdscript
func handle_deploy_phase():
    # Check for bonus armies (this will be updated when server awards armies)
    var bonus_armies = get_meta("pending_bonus_armies", 0)
    
    if bonus_armies > 0:
        await _deploy_bonus_armies(bonus_armies)
        # Clear the bonus armies after deployment
        set_meta("pending_bonus_armies", 0)
    
    Server.advance_phase(peer_id)
```

#### Bot Army Deployment Pattern
**Pattern**: Random territory selection with server-authoritative deployment
```gdscript
func _deploy_bonus_armies(army_count: int):
    # Recalculate owned territories (they might have changed due to conquests)
    _calculate_owned_territory_indexes()
    
    # Deploy armies to random territories using existing owned_territory_indexes
    for i in range(army_count):
        var random_index = owned_territory_indexes[randi() % owned_territory_indexes.size()]
        var territory_name = _get_territory_name_by_index(random_index)
        if not territory_name.is_empty():
            # Convert to underscore format for server
            var territory_name_underscore = territory_name.replace(" ", "_")
            
            # Use server's direct deployment function
            if Server:
                Server._deploy_army_to_territory_direct(territory_name_underscore, peer_id)
            
            # Small delay between deployments
            await get_tree().create_timer(0.2).timeout
```

#### Godot Inheritance Pattern
**Pattern**: Leverage Godot's Python-like inheritance for clean architecture
```gdscript
# Child classes inherit ALL parent methods automatically
# ChaoticBotPlayer can call:
# - _get_territory_name_by_index() ✅ (inherited from BotPlayer)
# - _get_neighbors_by_index() ✅ (inherited from BotPlayer)
# - _calculate_owned_territory_indexes() ✅ (inherited from BotPlayer)
# - All other BotPlayer utilities ✅

# No need for explicit abstract methods - just don't implement in base class
# Subclasses override only what they need to customize
```

#### Bot Type Specialization Pattern
**Pattern**: Each bot type focuses purely on decision-making while inheriting infrastructure
```gdscript
# Base class provides:
# - Territory management and calculation
# - Phase signal coordination
# - Utility methods for territory operations
# - Debug and logging infrastructure

# Specialized classes provide:
# - Phase-specific behavior (reinforce, attack, deploy, fortify)
# - Decision-making algorithms (random, strategic, etc.)
# - Bot-specific logic and timing
```

#### Extensible Bot Architecture Pattern
**Pattern**: Easy addition of new bot types without duplicating infrastructure
```gdscript
# To add new bot type (e.g., ExpertBotPlayer):
class_name ExpertBotPlayer extends BotPlayer
    # Inherits all infrastructure from BotPlayer
    # Only needs to implement decision-making methods
    func handle_attack_phase():
        # Expert-specific attack logic
        pass
```

### **NEW** Visual State Management Patterns

#### Double-Click Bug Prevention Pattern
**Pattern**: Comprehensive selection clearing on blocked turns to prevent visual state corruption
```gdscript
func _on_input_event(_viewport, event, _shape_idx):
    # Filter out Godot's double-click events to prevent timing conflicts
    if event is InputEventMouseButton and event.pressed and not event.double_click:
        
        # Check if turn is blocked
        if not _is_current_players_turn():
            _show_turn_blocked_feedback()
            # CRITICAL FIX: Clear ALL selections when not player's turn
            _clear_all_territory_selections_globally()
            return
```

#### Global Selection Clearing Pattern
**Pattern**: Nuclear approach to prevent territories getting stuck in selected visual state
```gdscript
func _clear_all_territory_selections_globally():
    """Clear ALL territory selections across the entire map - used when turn is blocked"""
    # Get all territories from map
    var map_node = get_node_or_null("/root/MainScene/Map")
    if map_node and map_node.has_method("get_all_territories"):
        var all_territories = map_node.get_all_territories()
        for territory in all_territories:
            if territory.has_node("MetaData"):
                territory.get_node("MetaData").set_meta("is_selected", false)
                territory._update_visual_state()
```

#### Visual State Security Pattern
**Pattern**: Prevent visual state corruption during multiplayer turn transitions
```gdscript
# Visual state tied to selection metadata
func _update_visual_state():
    var is_selected = $MetaData.get_meta("is_selected", false)
    if is_selected:
        # Show darker SelectedSprite
        selected_sprite.visible = true
        main_sprite.visible = false
    else:
        # Show normal MainSprite
        selected_sprite.visible = false
        main_sprite.visible = true
```

#### Godot Double-Click Handling Pattern
**Pattern**: Filter out engine double-click events to prevent input processing conflicts
```gdscript
# Ignore Godot's built-in double-click events
if event is InputEventMouseButton and event.pressed and not event.double_click:
    # Process single clicks only
    # Double-clicks are filtered out to prevent timing issues
```

### **NEW** Card Transfer System Patterns

#### Single Source of Truth Card Award Pattern
**Pattern**: Use existing card award system for all card operations instead of duplicating logic
```gdscript
# Modified _award_card_to_conqueror to accept optional card_path
func _award_card_to_conqueror(conqueror_peer_id: int, card_path: String = ""):
	"""Award card to conqueror - draws from deck if no path provided, uses specific card if provided"""
	if card_path.is_empty():
		# Normal conquest - draw random card from deck
		card_path = draw_card_from_deck()
	else:
		# Card transfer - use specific provided card
		print("Server: Awarding specific card (transfer): ", card_path)
	
	# Rest of existing card award logic handles all player types
	# - Bots: adds to server Player node
	# - Host: calls sync_card_award() for LocalPlayer visuals
	# - Clients: sends RPC with card award
```

#### Card Transfer Using Existing System Pattern
**Pattern**: Eliminate code duplication by routing transfers through existing award function
```gdscript
func _transfer_cards_from_eliminated_player(eliminated_player_id: int, eliminator_player_id: int):
	"""Transfer all cards from eliminated player to eliminator using existing award system"""
	# Get Cards node for eliminated player
	var eliminated_cards_node = eliminated_player.get_node_or_null("Cards")
	
	# Collect card paths from eliminated player
	var card_paths_to_transfer = []
	for card_instance in eliminated_cards_node.get_children():
		var card_path = card_instance.get_meta("card_path", "")
		if card_path.is_empty():
			card_path = card_instance.scene_file_path
		if not card_path.is_empty():
			card_paths_to_transfer.append(card_path)
	
	# Remove all cards from eliminated player
	for card_instance in eliminated_cards_node.get_children():
		eliminated_cards_node.remove_child(card_instance)
		card_instance.queue_free()
	
	# Award each card to eliminator using existing system
	for card_path in card_paths_to_transfer:
		_award_card_to_conqueror(eliminator_player_id, card_path)
```

#### Card Award Architecture Benefits
**Benefits of Single Source Pattern**:
- ✅ Eliminates ~80 lines of duplicate card creation logic
- ✅ Fixes ghost visual bug (host gets cards via proper `sync_card_award()` path)
- ✅ Consistent behavior for all player types (bots, host, clients)
- ✅ Single function to maintain and debug
- ✅ Respects player node architecture (server Player vs LocalPlayer separation)

**Why This Pattern Matters**:
- **Before**: Manual card creation bypassed existing systems → split state → ghost visuals
- **After**: All cards go through one function → consistent state → no ghost visuals
- **Principle**: Don't bypass existing systems - extend them to handle new use cases

### **NEW** Input Blocking During Animations Pattern

#### Simple Boolean Flag Pattern
**Pattern**: Block user input during animation sequences using boolean flag with RPC sync
```gdscript
# Server-side flag
var processing_card_trades: bool = false

# Set flag before animation sequence
processing_card_trades = true
sync_processing_card_trades.rpc(true)

# ... animation sequence (card trades, awards, removal) ...

# Clear flag after sequence complete
processing_card_trades = false
sync_processing_card_trades.rpc(false)
```

#### RPC Synchronization Pattern
**Pattern**: Sync animation state to all clients for consistent input blocking
```gdscript
@rpc("authority", "call_remote", "reliable")
func sync_processing_card_trades(processing: bool):
	"""Sync card trade processing state to clients"""
	processing_card_trades = processing
```

#### Input Handler Check Pattern
**Pattern**: Check blocking flag early in input handlers to prevent unwanted actions
```gdscript
func _on_input_event(_viewport, event, _shape_idx):
	if event is InputEventMouseButton and event.is_pressed():
		# Block clicks during card trade processing
		if Server and Server.processing_card_trades:
			return  # Early return - no further processing
		
		# ... normal input handling ...
```

#### Input Blocking Benefits
**Benefits of Boolean Flag Approach**:
- ✅ Simple implementation - just one boolean variable
- ✅ Minimal code changes - 5 small modifications total
- ✅ Works for all players - RPC syncs to clients automatically
- ✅ No race conditions - flag set before loop, cleared after
- ✅ Clean separation - server controls flag, handlers check flag

**Why This Pattern Matters**:
- **Problem**: Players could click territories during multi-trade animation sequences
- **Result**: Premature phase advancement, interrupted animations, confusion
- **Solution**: Block all territory input while animations running
- **Principle**: Block input during animations, re-enable after complete

### **NEW** Player Elimination System Patterns

#### Elimination Detection Pattern
**Pattern**: Detect and handle complete player elimination from game
```gdscript
func _handle_player_elimination(eliminated_player_id: int, eliminator_player_id: int):
	print("=== PLAYER ELIMINATION ===")
	print("Player ", eliminated_player_id, " eliminated by player ", eliminator_player_id)
	
	# Transfer all cards using existing award system (no duplication!)
	_transfer_cards_from_eliminated_player(eliminated_player_id, eliminator_player_id)
	
	# Award conquest card to eliminator (they conquered a territory)
	_award_card_to_conqueror(eliminator_player_id)
	
	# Remove from turn rotation
	for i in range(player_queue.size()):
		if player_queue[i].peer_id == eliminated_player_id:
			player_queue.remove_at(i)
			break
	
	# Award bonus units to eliminator
	var bonus_units = 5  # Standard elimination bonus
	award_units_to_player(eliminator_player_id, bonus_units)
```

#### Turn System Integration Pattern
**Pattern**: Seamlessly handle eliminated players in turn rotation
```gdscript
# Check if current player was eliminated
if current_turn_index >= player_queue.size():
    current_turn_index = 0  # Wrap around
    
# Ensure we still have players
if player_queue.size() == 0:
    current_phase = GamePhase.GAME_OVER
    return
```

#### Elimination Reward Pattern
**Pattern**: Reward players for complete elimination of opponents
```gdscript
func award_elimination_bonus(eliminator_id: int):
    var bonus_units = 5
    var territories = get_player_territories(eliminator_id)
    
    if territories.size() > 0:
        # Add bonus to random territory
        var random_territory = territories[randi() % territories.size()]
        random_territory.set_unit_count(random_territory.get_unit_count() + bonus_units)
        
    print("Player ", eliminator_id, " awarded ", bonus_units, " units for elimination")
```

## Component Relationships
```
Server (Node)
├── Game State Management
│   ├── Game Phases (REINFORCE, ATTACK, DEPLOY, FORTIFY, GAME_OVER)
│   ├── Turn Management
│   ├── Conquest Tracking (conquerors_this_turn)
│   ├── Deck Management (game_deck, depot)
│   ├── Attack of Despair (Globals.ATTACK_OF_DESPAIR)
│   └── Player Elimination (player_queue management)
├── Combat Resolution
│   ├── Dice Rolling
│   ├── Damage Calculation (Milos Rules)
│   ├── Conquest Handling
│   ├── Post-Conquest Movement
│   ├── AoD Combat Validation
│   └── Anti-Chain Attack Logic
├── Elimination System
│   ├── Elimination Detection (_handle_player_elimination)
│   ├── Card Transfer (_transfer_cards_from_eliminated_player)
│   ├── Conquest Card Award (_award_card_to_conqueror)
│   ├── Turn Queue Updates (player removal)
│   ├── Bonus Unit Awards (elimination rewards)
│   └── Game Over Detection (last player standing)
└── Signal System
    ├── phase_changed
    ├── turn_changed
    └── sync_attack_of_despair

NorthAmerica (Node2D)
├── Territory1 (Area2D + territories.gd)
│   ├── CollisionPolygon2D
│   ├── MainSprite (Sprite2D)
│   ├── UnitLabel (Label)
│   └── MetaData (Node)
├── Territory2 (Area2D + territories.gd)
└── ...

PlayerQueueHUD (Control)
├── Container (PanelContainer) 
│   └── VBox (VBoxContainer)
│       ├── TitleLabel (Label)
│       └── PlayerListContainer (VBoxContainer)
│           ├── Player1Label (Label - dynamic)
│           ├── Player2Label (Label - dynamic)
│           └── ... (scales with player count)

CombatDisplay (Control)
├── AttackerAvatarContainer (Control)
│   └── AttackerAvatar (PlayerAvatar instance)
├── AttDice1Container (Control)
│   └── DiceRect (TextureRect)
├── AttDice2Container (Control)
│   └── DiceRect (TextureRect)
├── AttDice3Container (Control)
│   └── DiceRect (TextureRect)
├── ResultLabelContainer (Control)
│   └── ResultLabel (Label)
├── DefDice1Container (Control)
│   └── DiceRect (TextureRect)
├── DefDice2Container (Control)
│   └── DiceRect (TextureRect)
├── DefDice3Container (Control)
│   └── DiceRect (TextureRect)
├── DefenderAvatarContainer (Control)
│   └── DefenderAvatar (PlayerAvatar instance)
├── FightCloudSprite (Sprite2D)
├── AttackSoundPlayer (AudioStreamPlayer)
├── HideTimer (Timer)
├── FadeTimer (Timer)
└── ShuffleTimer (Timer)

DeckManager (RefCounted)
├── Depot System (Array)
├── Balanced Deck Creation
│   ├── 37 Territory Cards
│   ├── 2 Treasure Cards
│   └── Unit Type Distribution (pirate, mount, cannon)
└── Card Infrastructure
    ├── All Territory Variants
    ├── Asset Integration
    └── Scene Structure

AudioManager (Node - Autoload)
├── Background Music Management
│   ├── Menu Music Player (menu_intermezzo.ogg)
│   ├── Game Music Player (ingame_relax.ogg)
│   ├── Scene-Based Detection
│   ├── Two-Step Game State Detection
│   └── Fade Transitions with Overlap
└── Music State Tracking
    ├── MENU_SPACE
    ├── GAME_SPACE
    └── NONE

MultiplayerScene (Node)
├── HUD Components
│   ├── TurnControls (Control)
│   ├── LocalPlayer (PlayerAvatar - LOCAL_PLAYER mode)
│   ├── TurnOrder (VBoxContainer - PlayerAvatar instances)
│   ├── MissionDisplay (Control)
│   ├── ContinentInfoHUD (Control)
│   ├── HandDisplay (Control)
│   ├── CombatDisplay (Control)
│   └── InGameMenu (Control)
├── Popup Container
│   ├── VictoryPopup (Control)
│   ├── EliminationPopup (Control)
│   ├── HostEliminationPopup (Control)
│   ├── AttackOfDespairPopup (Control)
│   ├── AttackConfirmPopup (Control)
│   ├── ConquestMovementPopup (Control)
│   └── ArmyAwardPopup (Control)
└── Audio Players
    ├── Popup Open/Close Players
    └── Victory/Defeat Players

PlayerAvatar (Control)
├── Display Modes
│   ├── LOCAL_PLAYER (constant local player display)
│   └── CURRENT_PLAYER (updates with turn changes)
├── Visual Components
│   ├── Panel (StyleBoxFlat - circular shape)
│   ├── OutlineRope (TextureRect)
│   ├── AnimalImage (TextureRect)
│   ├── ArmyCounter (Control with Label)
│   └── CardCounter (Control with Label)
└── Responsive Scaling
    ├── Dynamic Size Calculation
    ├── Circular Shape Maintenance
    └── Proportional Image Scaling
```

## Data Flow

### **NEW** Server Authority Flow
1. **Client Action** → **RPC to Server** → **Server Validation**
2. **Server Decision** → **Game State Update** → **Phase Progression**
3. **State Broadcast** → **All Clients Sync** → **UI Updates**
4. **Signal Emission** → **Phase/Turn Changes** → **Visual Feedback**

### **NEW** Game Phase Flow
1. **Game Action** → **Server Validation** → **Phase Check**
2. **Phase Logic** → **Automatic Progression** → **New Phase**
3. **Phase Change** → **Signal Emission** → **UI Updates**
4. **Turn Integration** → **Player Rotation** → **Phase Reset**

### **NEW** Deck Management Flow
1. **Game Start** → **Deck Creation** → **Balanced Distribution**
2. **Card Drawing** → **Player Hand** → **Card Display**
3. **Card Discard** → **Depot Storage** → **Reshuffle Ready**
4. **Deck Empty** → **Depot Reshuffle** → **New Deck**

### **NEW** Post-Conquest Movement Flow
1. **Successful Conquest** → **Overrun Check** → **Post-Conquest Mode**
2. **Mode Activation** → **Territory Selection** → **Continued Movement**
3. **User Confirmation** → **Mode Exit** → **Normal Gameplay**
4. **Conquest Tracking** → **Bonus Calculation** → **Strategic Rewards**

### Territory System
1. **Game Logic** → `set_unit_count()` → **MetaData storage**
2. **MetaData change** → `_update_unit_label()` → **Visual update**
3. **Ownership change** → `set_owner_id()` → **Color update**
4. **User interaction** → **Territory events** → **Game system response**

### **NEW** Multiplayer Synchronization Flow
1. **Client Action** → **RPC to Server** → **Server Validation**
2. **Server Decision** → **RPC Response** → **Client Confirmation**
3. **Client Update** → **Signal Emission** → **UI Refresh**
4. **State Broadcast** → **All Clients Sync** → **Consistent State**

### **NEW** Player Queue Update Flow
1. **Player Join/Leave** → **MultiplayerManager Signal** → **_update_player_list()**
2. **Display ID Change** → **display_ids_updated Signal** → **_update_player_list()**
3. **Player Count Change** → **_resize_container_for_players()** → **Dynamic Sizing**

### **NEW** Combat Visual Display Flow
1. **Combat Resolution** → **sync_combat_result()** → **Combat Display Show**
2. **Question Mark Phase** → **_show_question_marks()** → **Shuffle Timer Start** → **Random Dice Shuffling (2 seconds)**
3. **Dice Reveal** → **Shuffle Timer Stop** → **_display_dice()** → **Actual Dice Textures Displayed**
4. **Result Analysis** → **_determine_result_text()** → **Result Label Update**
5. **Auto-Hide Timer** → **5-Second Delay** → **Display Hide**

## Design Principles
- **Single Responsibility**: Each node has one clear purpose
- **Composition over Inheritance**: Features added via child nodes
- **Event-driven**: Updates triggered by changes, not polling
- **Fail-safe**: Graceful handling of missing components
- **Server Authority**: Server validates all critical game state changes
- **Client Prediction**: UI updates immediately with server confirmation
- **Comprehensive Logging**: Debug-friendly with clear state tracking
- **Responsive Design**: UI adapts dynamically to content and player count
- **Minimal Refactoring**: User preference for minimal necessary changes [[memory:2465470]]
- **Edge Case Handling**: Robust systems that prevent impossible states (negative units, mutual destruction)
- **Single Source of Truth**: Eliminate code duplication for maintainability
- **Visual Feedback**: Clear, immediate feedback for all user actions
- **Clean UI**: Auto-hiding elements that don't interfere with gameplay
- **Structured Gameplay**: Clear phase progression with strategic depth
- **Balanced Systems**: Fair distribution and balanced gameplay mechanics
- **Strategic Depth**: Multiple layers of decision-making and tactical options

### **NEW** Bot Phase Timing Pattern
- **Deferred Phase Advances**: `call_deferred("_advance_phase_deferred")` prevents bot signal skipping
- **Server-Bot Communication**: Direct server function calls for reliable army management  
- **Phase Signal Integrity**: Ensures bots properly receive and process all phase change signals
- **Auto-Advance Prevention**: Server respects bot pending armies before auto-advancing DEPLOY phase

### **NEW** Bot Deployment Isolation Pattern
**Pattern**: Complete isolation of bot deployment to prevent phase interference
```gdscript
# Server-side deployment flag to block all phase advancement
var bot_deployment_in_progress: bool = false

# Block phase advancement during bot deployment
func advance_phase(requesting_peer_id: int):
    if bot_deployment_in_progress:
        return  # Block all phase changes

# Set flag when bot starts deployment
bot_deployment_in_progress = true
phase_changed.emit(GamePhase.DEPLOY)
await bot_player.deployment_completed
bot_deployment_in_progress = false
```

### **NEW** Signal-Based Async Completion Pattern
**Pattern**: Server waits for bot completion using signal-based async waiting
```gdscript
# Bot emits completion signal after deployment
signal deployment_completed

# Server waits for completion
var bot_player = find_player_by_peer_id(current_player_peer_id)
if bot_player and bot_player.has_signal("deployment_completed"):
    await bot_player.deployment_completed
```

### **NEW** Debug Cleanup Strategy Pattern
**Pattern**: Systematic removal of debug noise while preserving essential functionality
```gdscript
# Remove verbose debug sections
# ❌ Remove: "=== DEBUG SECTION ===" headers
# ❌ Remove: Step-by-step logging
# ❌ Remove: Redundant status messages
# ✅ Keep: Essential error messages
# ✅ Keep: Mission assignment messages
# ✅ Keep: Critical game state changes
```

### NEW Rookie Bot Strategy Patterns

#### Mission Factor Constants Pattern
**Pattern**: Centralized mission factor configuration for maintainability
```gdscript
# Constants
const MISSION_FACTOR = 2
const DEPLOY_MULTIPLIER = 5
```

#### Rookie Bot Mission-Weighted Targeting Pattern
**Pattern**: Mission-aware weight multiplier integrated into attack evaluation
```gdscript
func _calculate_mission_factor(defender_territory_name: String) -> int:
	var mission = _get_current_mission()
	if mission.type == "elimination":
		return MISSION_FACTOR if _is_territory_owned_by_mission_target(defender_territory_name) else 1
	if mission.type == "conquest":
		return _get_continent_of_choice_mission_factor(defender_territory_name) if mission.get("any_third", false) else (MISSION_FACTOR if _is_territory_in_mission_continent(defender_territory_name) else 1)
	if mission.id == "sTriple":
		var cont = _get_territory_continent(defender_territory_name)
		return _get_three_continents_mission_factor(cont)
	return 1
```

#### Strategic Deployment Mission Factor Pattern
**Pattern**: Enhanced deployment factor considering both territory and attack potential
```gdscript
func _calculate_deployment_mission_factor(territory_name: String) -> int:
	# Base mission factor for the territory itself
	var base_factor = _calculate_mission_factor(territory_name)
	
	# Calculate attack potential - sum mission factors of all attackable neighbors
	var attack_potential = 0
	var territory_index = _get_territory_index_by_name(territory_name)
	var neighbors = _get_neighbors_by_index(territory_index)
	
	for neighbor_index in neighbors:
		if not owned_territory_indexes.has(neighbor_index):  # Enemy territory
			var neighbor_name = _get_territory_name_by_index(neighbor_index)
			attack_potential += _calculate_mission_factor(neighbor_name)
	
	# Total deployment factor = base + attack potential (no cap, same weight)
	return base_factor + attack_potential
```

#### Mission-Aware Deployment Pattern
**Pattern**: Strategic army placement based on mission factors and attack potential
```gdscript
func _deploy_bonus_armies(army_count: int):
	# Calculate weighted deployment options based on mission factors
	var deployment_options = _calculate_deployment_weights()
	
	# Deploy armies using weighted selection
	for i in range(army_count):
		var selected_territory = _select_deployment_territory(deployment_options)
		# Deploy to selected territory with mission-based priority
```

#### Deployment Weight Calculation Pattern
**Pattern**: Weighted deployment options with mission factor multipliers
```gdscript
func _calculate_deployment_weights() -> Array:
	var deployment_options = []
	
	for territory_index in owned_territory_indexes:
		var territory_name = _get_territory_name_by_index(territory_index)
		
		# Calculate enhanced mission factor for deployment (territory + attack potential)
		var mission_factor = _calculate_deployment_mission_factor(territory_name)
		if mission_factor > 1:
			mission_factor *= DEPLOY_MULTIPLIER
		
		deployment_options.append({
			"territory_name": territory_name,
			"weight": float(mission_factor)
		})
	
	# Normalize weights to probabilities
	return deployment_options
```

#### Continent-of-Choice Helper Pattern
**Pattern**: Fast-path + exclusion + best-continent selection
```gdscript
func _get_continent_of_choice_mission_factor(defender_territory_name: String) -> int:
	# Fast-path: if territory already in mission continents, prefer immediately
	if _is_territory_in_mission_continent(defender_territory_name):
		return MISSION_FACTOR
	var eligible = _get_all_continents()
	# Exclude fixed mission continents from eligibility pool
	var current_mission = _get_current_mission()
	for c in current_mission.get("continents", []):
		if eligible.has(c):
			eligible.erase(c)
	# Then select continent with least missing territories
```

#### Territory→Continent Mapping Cache Pattern
**Pattern**: Load `territory_continent_map` once in `_ready()` to avoid repeated I/O
```gdscript
func _ready():
	territory_continent_map = _load_territory_continent_mapping()
```

#### Top-N Attack Option Limiting Pattern
**Pattern**: Reduce randomness by sampling from best options only
```gdscript
attack_options.sort_custom(func(a, b): return a.weight > b.weight)
attack_options = attack_options.slice(0, 3)
var total_weight := 0.0
for o in attack_options:
	total_weight += o.weight
for o in attack_options:
	o.probability = o.weight / total_weight if total_weight > 0 else 0.0
```

### **NEW** Rookie Bot Attack Limiting Patterns

#### Attack Attempt Limiting Pattern
**Pattern**: Limit bot attack attempts per turn to prevent infinite loops and improve strategic behavior
```gdscript
var loop_count = 0
while true:
	loop_count += 1
	if loop_count > 3:  # Limit to 3 attack attempts
		print(">>> BOT ", player_name, ": Attack limit (3) reached, ending attack phase")
		break
	# ... attack logic ...
```

#### Tiered Attack Selection Pattern
**Pattern**: Progressive attack option filtering based on weight thresholds
```gdscript
# Overrun mode: only select options with weight > MISSION_FACTOR
if overrun_mode:
	attack_options = attack_options.filter(func(option): return option.weight > MISSION_FACTOR)
	# take top 2 options
	attack_options = attack_options.slice(0, 2)
else:
	var filtered_options = attack_options.filter(func(option): return option.weight > MISSION_FACTOR)
	if filtered_options.size() > 0:
		attack_options = filtered_options
		# take top 3 options
		attack_options = attack_options.slice(0, 3)
	else:
		filtered_options = attack_options.filter(func(option): return option.weight > 1)
		if filtered_options.size() > 0:
			attack_options = filtered_options
			# take top 4 options
			attack_options = attack_options.slice(0, 4)
		else:
			# take top 5 options
			attack_options = attack_options.slice(0, 5)
```

### **NEW** Visual Feedback Management Patterns

#### Invalid Click Feedback Disabling Pattern
**Pattern**: Disable visual feedback for invalid territory clicks during player turns to prevent UI confusion
```gdscript
# Player clicked enemy territory during DEPLOY - block action
# _show_turn_blocked_feedback()  # DISABLED: No visual feedback for invalid clicks
return
```

#### Clean UI Experience Pattern
**Pattern**: Provide clean user experience by removing confusing visual feedback during inappropriate actions
```gdscript
# Check if this is an enemy territory click during REINFORCE phase (auto-switch)
if Server and Server.get_current_phase() == Server.GamePhase.REINFORCE:
	if not _is_owned_by_current_player():
		# No visual feedback for invalid clicks - clean UI experience
		# Only show attack confirmation for valid adjacent attacks
```

### **NEW** Debug Logging Refactoring Patterns

#### Systematic Debug Cleanup Pattern
**Pattern**: Comment out debug statements while preserving essential functionality
```gdscript
# # print("=== DEBUG SECTION ===")  # Commented out for clean output
# # print("Current state: ", variable)  # Preserved for debugging if needed
print("Essential error message")  # Keep critical error reporting
```

#### Selective Debug Preservation Pattern
**Pattern**: Keep essential debug output while removing verbose logging
```gdscript
# ❌ Remove: "=== DEBUG SECTION ===" headers
# ❌ Remove: Step-by-step logging
# ❌ Remove: Redundant status messages
# ✅ Keep: Essential error messages
# ✅ Keep: Mission assignment messages
# ✅ Keep: Critical game state changes
```

#### Debug Output Statistics Pattern
**Pattern**: Track debug cleanup progress across multiple files
```gdscript
# Files with debug cleanup:
# - Players/Rookie/rookie_bot_player.gd: 47 commented statements
# - Map/territories.gd: 103 commented statements  
# - Players/player.gd: 39 commented statements
# - HUD/hand_display.gd: 3 commented statements
# - server.gd: 393 commented statements
# Total: 585+ debug statements systematically cleaned up
```

### **NEW** Enhanced Bot Elimination Patterns

#### Comprehensive Card Transfer Pattern
**Pattern**: Complete card transfer from eliminated player to eliminator with proper rewards
```gdscript
func _handle_player_elimination(eliminated_player_id: int, eliminator_player_id: int):
	# Transfer all cards from eliminated player to eliminator
	_transfer_cards_from_eliminated_player(eliminated_player_id, eliminator_player_id)
	
	# Award conquest card to eliminator (they conquered a territory)
	_award_card_to_conqueror(eliminator_player_id)
	
	# Remove eliminated player from turn queue and handle turn management
	_remove_player_from_turn_queue(eliminated_player_id)
```

#### Bot Elimination Reward Pattern
**Pattern**: Proper reward handling for bot elimination with both card transfer and conquest bonus
```gdscript
func _transfer_cards_from_eliminated_player(eliminated_player_id: int, eliminator_player_id: int):
	"""Transfer all cards from eliminated player to eliminator"""
	if not multiplayer.is_server():
		print("ERROR: Not server - cannot transfer cards")
		return  # Only server handles card transfers
	
	# Get eliminated player's cards and transfer to eliminator
	# Handle both human and bot players appropriately
```

### **NEW** Hand Display Enhancement Patterns

#### Multi-Container Layout Validation Pattern
**Pattern**: Ensure all card containers are properly initialized before use
```gdscript
func _is_hand_ready() -> bool:
	"""Check if hand display is ready and has card containers"""
	if card_container == null or card_container2 == null or card_container3 == null:
		return false
	return true
```

#### Comprehensive Layout Information Pattern
**Pattern**: Provide complete layout information for all 12 card positions
```gdscript
func get_layout_info() -> Dictionary:
	"""Get all layout information for cards"""
	if not _is_hand_ready():
		return {}
	
	var layout = {}
	for i in range(main_hand_positions.size()):
		layout[i] = {
			"position": get_card_position(i),
			"size": get_card_size(i)
		}
	
	return layout
```

#### Placeholder Initialization Pattern
**Pattern**: Initialize all 12 card placeholder positions with proper null checking
```gdscript
var placeholder_nodes = [
	$CardContainer/Card1, $CardContainer/Card2, $CardContainer/Card3, $CardContainer/Card4, $CardContainer/Card5,
	$CardContainer2/Card6, $CardContainer2/Card7, $CardContainer2/Card8, $CardContainer2/Card9,
	$CardContainer3/Card10, $CardContainer3/Card11, $CardContainer3/Card12
]

var first_placeholder = true
for placeholder in placeholder_nodes:
	if placeholder != null:
		main_hand_positions.append(placeholder.global_position)
		if first_placeholder:
			main_hand_size = placeholder.size
			first_placeholder = false
		placeholder.visible = false  # Keep placeholders hidden as layout guides
```

### **NEW** Card Award Display Patterns

#### Card Award Display with Fade Animation Pattern
**Pattern**: Display traded cards in center screen before bonus popup, with synchronized fade animation
```gdscript
func show_traded_cards(card_instances: Array):
	"""Show traded cards in CardsAwarded container and fade them out over 3 seconds"""
	# Hide combat display to prevent overlap
	_hide_combat_display()
	
	# Move cards to CardsAwarded container and position them
	for i in range(card_instances.size()):
		if i < cards_awarded_positions.size():
			var card_position = cards_awarded_positions[i]
			var card_size = cards_awarded_size
			_position_card_in_awarded(card_instance, card_position, card_size)
	
	# Start fade animation (3 seconds, synchronized with bonus popup)
	_fade_out_cards(card_instances)
```

#### Placeholder-Based Card Positioning Pattern
**Pattern**: Use placeholder positions (Card10, Card11, Card12) for absolute positioning in CardsAwarded container
```gdscript
func _initialize_cards_awarded_placeholders():
	"""Initialize placeholder positions for CardsAwarded container"""
	var placeholder_nodes = [
		cards_awarded_container.get_node_or_null("Card10"),
		cards_awarded_container.get_node_or_null("Card11"),
		cards_awarded_container.get_node_or_null("Card12")
	]
	
	for placeholder in placeholder_nodes:
		if placeholder != null:
			cards_awarded_positions.append(placeholder.position)
			placeholder.visible = false  # Keep hidden as layout guide
```

#### Tween-Based Fade Animation Pattern
**Pattern**: Smooth fade-out animation using Tween with parallel animations for multiple cards
```gdscript
func _fade_out_cards(card_instances: Array):
	"""Fade out cards over 3 seconds using Tween"""
	var tween = create_tween()
	tween.set_parallel(true)
	
	for card_instance in card_instances:
		if card_instance is Control:
			tween.tween_property(card_instance, "modulate:a", 0.0, 3.0)
		# Handle Node2D and Node types similarly
	
	# Clean up after fade completes
	tween.finished.connect(_cleanup_faded_cards.bind(card_instances))
```

#### Card Removal with Instance Preservation Pattern
**Pattern**: Return both paths and instances from card removal to allow display before data cleanup
```gdscript
func remove_cards_by_type(cards_to_remove: Array) -> Dictionary:
	"""Return dictionary with 'paths' and 'instances' of removed cards"""
	var removed_paths = []
	var removed_instances = []
	
	# Collect instances before removing
	for card_type_to_remove in cards_to_remove:
		# Find matching card instance
		removed_instances.append(card_instance)
		removed_paths.append(card_path)
	
	# Don't queue_free() removed instances yet - let hand_display handle cleanup
	# Only queue_free() cards that are staying
	
	return {"paths": removed_paths, "instances": removed_instances}
```

#### Combat Display Cleanup Pattern
**Pattern**: Automatically hide/remove combat display when awarded cards are shown to prevent visual overlap
```gdscript
func _hide_combat_display():
	"""Hide or remove combat display to prevent overlap with awarded cards"""
	var main_scene = get_tree().get_first_node_in_group("main_scene")
	if main_scene:
		# Check for static CombatDisplay node
		var combat_display = main_scene.get_node_or_null("CombatDisplay")
		if combat_display:
			combat_display.queue_free()
		
		# Check for dynamically created instances
		for child in main_scene.get_children():
			if child.name == "CombatDisplay":
				child.queue_free()
```

### **NEW** Territory Bonus Card Animation Patterns

#### Territory Bonus Information Passing Pattern
**Pattern**: Server creates territory_bonus_map and passes to clients via RPC for animation system
```gdscript
# Server-side: Create mapping of card paths to territory names
var territory_bonuses = process_territory_bonuses(removed_paths, peer_id)
var territory_bonus_map = {}
for card_path in removed_paths:
    var path_parts = card_path.split("/")
    if path_parts.size() >= 5:
        var territory_name = path_parts[4].replace(" ", "_")
        if territory_bonuses.has(territory_name):
            territory_bonus_map[card_path] = territory_name
        else:
            territory_bonus_map[card_path] = null

# Pass to client via RPC
sync_card_removal_to_client.rpc_id(peer_id, peer_id, cards_used, territory_bonus_map)
```

#### Delayed Card Movement Animation Pattern
**Pattern**: Cards appear in center, wait 3 seconds, then move toward territories with scale and fade
```gdscript
func _animate_cards_to_territories(card_instances: Array, territory_bonus_map: Dictionary):
    # Start fade immediately (5 seconds total)
    var fade_tween = create_tween()
    fade_tween.set_parallel(true)
    for visual_node in all_visual_nodes:
        fade_tween.tween_property(visual_node, "modulate:a", 0.0, 5.0)
    
    # Wait 3 seconds before movement
    await get_tree().create_timer(3.0).timeout
    
    # Start movement and scale animations (1 second)
    var movement_tween = create_tween()
    movement_tween.set_parallel(true)
    # ... movement and scale animations
```

#### Center-Aligned Card Movement Pattern
**Pattern**: Card centers align with territory centers using dynamic size calculation
```gdscript
# Get actual card size (scalable for window resizing)
var card_size = visual_node.size  # For Control nodes
var card_center_offset = card_size / 2.0

# Adjust target so card center aligns with territory center
var territory_center = _get_territory_screen_position(territory_name)
var adjusted_target = territory_center - card_center_offset

# Animate to adjusted position
movement_tween.tween_property(visual_node, "position", adjusted_target, 1.0)
```

#### Differentiated Scale Animation Pattern
**Pattern**: Territory bonus cards and non-bonus cards have different scale animations
```gdscript
# Territory bonus cards: shrink from 1.0 to 0.2 over 1 second
for visual_node in bonus_visual_nodes:
    visual_node.scale = Vector2(1.0, 1.0)  # Initial
    movement_tween.tween_property(visual_node, "scale", Vector2(0.2, 0.2), 1.0)

# Non-bonus cards: shrink from 1.0 to 0.0 over 0.5 seconds
for visual_node in non_bonus_visual_nodes:
    visual_node.scale = Vector2(1.0, 1.0)  # Initial
    movement_tween.tween_property(visual_node, "scale", Vector2(0.0, 0.0), 0.5)
```

#### Territory Bonus Display Pattern
**Pattern**: Show "+2" above territory when bonus card arrives, matching continent bonus style
```gdscript
func _show_territory_bonus_display(territory_name: String, bonus_value: int):
    # Get territory center
    var territory = map.find_territory(territory_name)
    var territory_center = map.get_territory_center(territory)
    
    # Create label (same style as continent bonus)
    var bonus_label = Label.new()
    bonus_label.text = "+" + str(bonus_value)
    bonus_label.add_theme_font_size_override("font_size", 64)
    bonus_label.modulate = Color(1.0, 0.84, 0.0, 0.0)  # Gold, start invisible
    
    # Animate upward and fade in (same as continent bonus)
    var tween = bonus_label.create_tween()
    tween.set_parallel(true)
    tween.tween_property(bonus_label, "position", territory_center + Vector2(0, -150), 2.5)
    tween.tween_property(bonus_label, "modulate:a", 1.0, 1.2)
    
    # Cleanup after 3 seconds
    tween.finished.connect(func():
        await get_tree().create_timer(3.0).timeout
        bonus_label.queue_free()
    )
```

### **NEW** Popup System Architecture Patterns

#### Popup System Overview
**Pattern**: Comprehensive popup system with 7 distinct popup types for different game events
```gdscript
# Popup types:
# 1. VictoryPopup - End game victory/defeat display
# 2. EliminationPopup - Player elimination notification
# 3. HostEliminationPopup - Special host elimination handling
# 4. AttackOfDespairPopup - Attack of Despair mode notification
# 5. AttackConfirmPopup - Attack confirmation dialog
# 6. ConquestMovementPopup - Post-conquest movement dialog
# 7. ArmyAwardPopup - Bonus army award display
```

#### Popup Audio Pattern
**Pattern**: Consistent audio system across all popups with open/close sounds
```gdscript
# Standard popup audio setup
var popup_open_player: AudioStreamPlayer
var popup_close_player: AudioStreamPlayer

func _ready():
    popup_open_player = AudioStreamPlayer.new()
    popup_close_player = AudioStreamPlayer.new()
    add_child(popup_open_player)
    add_child(popup_close_player)

func _play_open_sound():
    var sound_path = "res://Assets/SoundEffects/popup_open.ogg"
    popup_open_player.volume_db = -15.0
    popup_open_player.play()

func _play_close_sound():
    var sound_path = "res://Assets/SoundEffects/popup_close.ogg"
    popup_close_player.volume_db = -15.0
    popup_close_player.play()
```

#### Popup Signal Pattern
**Pattern**: Signal-based communication for popup actions
```gdscript
# Standard popup signals
signal confirmed
signal canceled

# Usage in parent scene
popup.confirmed.connect(_on_popup_confirmed)
popup.canceled.connect(_on_popup_canceled)
```

#### Victory Popup Pattern
**Pattern**: End game popup with victory/defeat detection and game pause
```gdscript
func show_victory_popup(winner_name: String, mission: Dictionary):
    # Pause game completely
    get_tree().paused = true
    Server.is_paused = true
    
    # Determine victory/defeat based on local player
    var local_player_name = MultiplayerManager.local_player_info.name
    var title = "VICTORY!" if winner_name == local_player_name else "DEFEAT!"
    
    # Play appropriate sound
    if winner_name == local_player_name:
        _play_victory_sound()
    else:
        _play_defeat_sound()
    
    victory_popup.setup(title, message)
    victory_popup.show_popup()
```

#### Elimination Popup Pattern
**Pattern**: Player elimination notification with stay/watch option
```gdscript
func setup(eliminator_name: String):
    message_label.text = "You were eliminated by " + eliminator_name + "!\n\nDo you want to stay and watch the game?"

# Host elimination popup has special message
func setup(eliminator_name: String):
    message_label.text = "You were eliminated by " + eliminator_name + "!\n\nAs the host, you must continue running the server for other players."
```

#### Attack Confirm Popup Pattern
**Pattern**: Confirmation dialog for auto-switching from REINFORCE to ATTACK phase
```gdscript
func setup(source_territory: String, target_territory: String):
    var source_name = _format_territory_name(source_territory)
    var target_name = _format_territory_name(target_territory)
    message_label.text = "Switch to Attack phase and\nattack " + target_name + " from " + source_name + "?"
```

#### Conquest Movement Popup Pattern
**Pattern**: Post-conquest movement confirmation dialog
```gdscript
# Simple confirmation dialog
# Action button: "Finish" (confirmed signal)
# Back button: "Keep Moving" (canceled signal)
```

#### Army Award Popup Pattern
**Pattern**: Auto-hiding popup with animation to avatar position
```gdscript
func show_army_award(army_count: int):
    message_label.text = "You received " + str(army_count) + " armies!"
    visible = true
    _play_popup_open_sound()
    hide_timer.start()  # 6 second auto-hide
    
    # After 5 seconds, animate to avatar bottom
    await get_tree().create_timer(5.0).timeout
    _animate_to_avatar_bottom()  # 1.1 second animation with scale shrink
```

### **NEW** Complete Audio System Architecture Patterns

#### Audio Manager System Pattern
**Pattern**: Centralized background music management with scene-based transitions
```gdscript
# AudioManager autoload manages all background music
enum MusicState { MENU_SPACE, GAME_SPACE, NONE }

# Two audio players for seamless transitions
var menu_music_player: AudioStreamPlayer
var game_music_player: AudioStreamPlayer

# Settings
var default_volume_db: float = -20.0
var overlap_time: float = 1.0
var fade_time: float = 1.0
```

#### Scene-Based Music Detection Pattern
**Pattern**: Automatic music switching based on scene name and game state
```gdscript
func _detect_music_state(scene_name: String) -> MusicState:
    # Menu space scenes
    if scene_name in ["MainMenu", "RulesScene"]:
        return MusicState.MENU_SPACE
    
    # Game space - MultiplayerScene with two-step check
    elif scene_name == "MultiplayerScene":
        if _is_game_active():  # Map visible + lobby hidden
            return MusicState.GAME_SPACE
        else:
            return MusicState.MENU_SPACE
```

#### Two-Step Game State Detection Pattern
**Pattern**: Accurate game state detection using map visibility and lobby visibility
```gdscript
func _is_game_active() -> bool:
    var map = multiplayer_scene.get_node_or_null("Map")
    if map and map.visible:
        var lobby = multiplayer_scene.get_node_or_null("Menu/SimpleLobby")
        if lobby:
            if lobby.visible:
                return false  # Menu music (in lobby)
            else:
                return true   # Game music (game active)
    return false
```

#### Fade Transition Pattern
**Pattern**: Smooth music transitions with overlap for seamless experience
```gdscript
func _fade_transition(from_player: AudioStreamPlayer, to_player: AudioStreamPlayer):
    # Start new music silent
    to_player.volume_db = -80.0
    to_player.play()
    
    # Fade out old music (1 second)
    var fade_out_tween = create_tween()
    fade_out_tween.tween_property(from_player, "volume_db", -80.0, fade_time)
    fade_out_tween.tween_callback(func(): from_player.stop())
    
    # Fade in new music (1 second, overlaps)
    var fade_in_tween = create_tween()
    fade_in_tween.tween_property(to_player, "volume_db", default_volume_db, fade_time)
```

#### Music Looping Pattern
**Pattern**: Automatic music restart when tracks finish
```gdscript
func _on_menu_music_finished():
    if current_music_state == MusicState.MENU_SPACE:
        menu_music_player.play()

func _on_game_music_finished():
    if current_music_state == MusicState.GAME_SPACE:
        game_music_player.play()
```

#### Sound Effect System Pattern
**Pattern**: Distributed sound effects across multiple systems
```gdscript
# Combat sounds
# - attack_punch_dice.wav (combat display, -10 dB)
# - Animal-specific attack sounds (deprecated, now unified)

# UI sounds
# - popup_open.ogg (-15 dB)
# - popup_close.ogg (-15 dB)
# - ui_select.ogg (in-game menu)

# Game event sounds
# - continent_rocks.wav (continent bonus, server.gd)
# - card_goldsteps.wav (card awards, hand_display.gd)
# - victory/defeat sounds (multiplayer_scene.gd, -5 dB)
```

#### Server Audio Integration Pattern
**Pattern**: Server-side audio for game events
```gdscript
# Server creates audio player for continent bonuses
continent_bonus_player = AudioStreamPlayer.new()
add_child(continent_bonus_player)

# Play sound when continent bonus awarded
var sound_path = "res://Assets/SoundEffects/continent_rocks.wav"
continent_bonus_player.stream = load(sound_path)
continent_bonus_player.play()
```

### **NEW** Visual UI Component System Patterns

#### Player Avatar System Pattern
**Pattern**: Dual-mode avatar system for local player and current turn player
```gdscript
enum DisplayMode { LOCAL_PLAYER, CURRENT_PLAYER }

# LOCAL_PLAYER: Always shows local player (constant)
# CURRENT_PLAYER: Updates to show current turn player

func _update_avatar_color():
    # Get current turn player from Server
    var current_turn_peer_id = Server.get_current_player_peer_id()
    var player_info = MultiplayerManager.players[current_turn_peer_id]
    set_player_color(player_info.color)
    set_animal_image(player_info.name)

func _update_local_player_color():
    # Get local player from MultiplayerManager
    var my_peer_id = multiplayer.get_unique_id()
    var player_info = MultiplayerManager.players[my_peer_id]
    set_player_color(player_info.color)
    set_animal_image(player_info.name)
```

#### Avatar Responsive Scaling Pattern
**Pattern**: Dynamic avatar sizing with circular shape maintenance
```gdscript
func update_avatar_size_and_style(new_size: Vector2):
    # Calculate corner radius for perfect circle
    var radius = min(new_size.x, new_size.y) / 2
    
    # Update all corner radii
    style_box.corner_radius_top_left = radius
    style_box.corner_radius_top_right = radius
    style_box.corner_radius_bottom_right = radius
    style_box.corner_radius_bottom_left = radius
    
    # Scale outline rope to match
    outline_rope.custom_minimum_size = new_size
    
    # Scale animal image proportionally (105% of circle, positioned at bottom)
    var image_size = new_size * 1.05
    animal_image.offset_top = -image_size.y - small_margin
```

#### Avatar Counters Pattern
**Pattern**: Army and card counters with automatic visibility management
```gdscript
# Army counter: Shows pending armies (hidden when 0)
func _update_army_counter():
    army_label.text = str(pending_armies)
    army_counter.visible = pending_armies > 0

# Card counter: Always visible, shows card count from MultiplayerManager
func _update_card_counter():
    var card_count = MultiplayerManager.players[represented_player_id].card_count
    card_label.text = str(card_count)
    card_counter.visible = true  # Always visible
```

#### Continent Info HUD Pattern
**Pattern**: Continent bonus display with font styling
```gdscript
# Font application
# - Title and points: PirataOne font (pirate theme)
# - Continent names: Estonia-Regular font

# Displays all 6 continents with bonus values:
# - Mudflats, Bamboovia, Riverside, Bushlands, Eucalypta, Peaks
```

#### Mission Display Pattern
**Pattern**: Responsive mission display with size adaptation
```gdscript
# Two size modes:
# - Standard missions: 1.5x width, 1.0x height
# - Elimination missions: 1.6x width, 2.1x height (larger for longer descriptions)

# Responsive positioning based on avatar size and viewport scaling
func _resize_for_elimination():
    var scale_factor = min(scale_x, scale_y)
    var scaled_size = base_avatar_size * scale_factor
    var mission_width = scaled_size.x * 1.6
    var mission_height = scaled_size.y * 2.1
```

#### In-Game Menu Pattern
**Pattern**: Pause menu with state management and scene transitions
```gdscript
# Menu options:
# - Resume: Hide menu, continue game
# - Rules: Show rules scene (in-game context)
# - Settings: Disabled (coming soon)
# - Exit Match: Reset multiplayer state, return to main menu

func _reset_multiplayer_state():
    # Reset global state
    Globals.ATTACK_OF_DESPAIR = false
    Globals.reset_single_player_mode()
    
    # Disconnect multiplayer peer
    multiplayer.multiplayer_peer = null
    
    # Clear MultiplayerManager
    MultiplayerManager.players.clear()
    
    # Reset Server state
    Server.game_players.clear()
    Server.player_queue.clear()
    # ... additional resets
```

#### Rules Scene Pattern
**Pattern**: Context-aware rules display (main menu vs in-game)
```gdscript
# Context detection
func _setup_context():
    var parent_scene = get_parent()
    if parent_scene.name == "MultiplayerScene":
        is_ingame_context = true
        background.color.a = 0.85  # Semi-transparent
        back_button.text = "BACK TO GAME"
    else:
        is_ingame_context = false
        background.color.a = 1.0  # Opaque
        back_button.text = "BACK TO MENU"

# Responsive margins (15% on each side)
func _setup_margins():
    var screen_width = get_viewport().get_visible_rect().size.x
    var margin_size = int(screen_width * 0.15)
    margin_container.add_theme_constant_override("margin_left", margin_size)
    margin_container.add_theme_constant_override("margin_right", margin_size)
```

### **NEW** Font System Patterns

#### Font Asset Pattern
**Pattern**: Two-font system for visual hierarchy
```gdscript
# Primary fonts:
# - PirataOne-Regular.ttf: Titles, points, headers (pirate theme)
# - Estonia-Regular.ttf: Body text, labels, descriptions

# Font loading
var pirate_font = load("res://Assets/Font_Pirata_One/PirataOne-Regular.ttf")
var estonia_font = load("res://Assets/Fonts/Estonia-Regular.ttf")
```

#### Font Application Pattern
**Pattern**: Consistent font usage across UI components
```gdscript
# Title labels: PirataOne
title_label.add_theme_font_override("font", pirate_font)
title_label.add_theme_color_override("font_color", Color(0, 0, 0, 1))

# Body labels: Estonia
description_label.add_theme_font_override("font", estonia_font)
description_label.add_theme_color_override("font_color", Color(0, 0, 0, 1))
```

### **NEW** Responsive Design System Patterns

#### Avatar Scaling System Pattern
**Pattern**: Viewport-based responsive scaling with base size references
```gdscript
# Base sizing variables (1920x1080 reference)
var base_avatar_size: Vector2 = Vector2(160, 160)
var base_avatar_margin: float = 20.0
var base_viewport_size: Vector2 = Vector2(1920, 1080)
var base_map_size: Vector2 = Vector2(1920, 1080)

# Scale calculation
func _update_avatar_scale():
    var current_viewport_size = get_viewport().get_visible_rect().size
    var scale_x = current_viewport_size.x / base_viewport_size.x
    var scale_y = current_viewport_size.y / base_viewport_size.y
    var scale_factor = min(scale_x, scale_y)  # Maintain aspect ratio
    
    var scaled_size = base_avatar_size * scale_factor
    var scaled_margin = base_avatar_margin * scale_factor
```

#### Viewport Change Handling Pattern
**Pattern**: Automatic UI updates on window resize
```gdscript
# Connect to viewport size changes
get_viewport().size_changed.connect(_on_viewport_size_changed)

func _on_viewport_size_changed():
    _update_avatar_scale()
    _update_mission_display_size()
    # ... update other responsive elements
```

#### Turn Order Container Scaling Pattern
**Pattern**: Dynamic turn order container sizing based on player count
```gdscript
# Base turn order dimensions
var base_turn_order_width: float = 200.0
var base_turn_order_height: float = 800.0

# Dynamic height calculation
func _resize_container_for_players(player_count: int):
    var height = base_height + (item_height * player_count)
    offset_top = -height - margin
```

### **NEW** Enhanced Combat Display Patterns

#### Attack Arrow Sprite Pattern
**Pattern**: Visual arrow indicator for attack direction
```gdscript
@onready var attack_arrow_sprite = $AttackArrowSprite
# Displays arrow pointing from attacker to defender
```

#### Damage Labels Pattern
**Pattern**: Separate damage display for attacker and defender
```gdscript
@onready var attacker_damage_label = $AttackerDamageLabel
@onready var defender_damage_label = $DefenderDamageLabel
# Shows unit losses for each side
```

#### Fade Timer System Pattern
**Pattern**: Gradual fade-out during display period
```gdscript
# Fade timing
var fade_start_time: float = 2.0  # Start fading after 2 seconds
var total_display_time: float = 5.0  # Total display duration

# Fade animation starts at fade_start_time, completes at total_display_time
```

#### Unified Attack Sound Pattern
**Pattern**: Single attack sound for all combat (replaces animal-specific sounds)
```gdscript
func _play_attack_sound(_combat_result: Dictionary):
    var sound_path = "res://Assets/SoundEffects/attack_punch_dice.wav"
    attack_sound_player.stream = load(sound_path)
    attack_sound_player.volume_db = -10  # Reduced volume
    attack_sound_player.play()
```

### **NEW** Unit Deployment Visual Effect Patterns

#### Dynamic Label Instance Pattern
**Pattern**: Create new label instance for each deployment to allow multiple simultaneous effects
```gdscript
func show_unit_deploy_effect(override_text: String = "") -> void:
    """Visual effect showing unit deployment - scale + fade animation"""
    # Create a NEW label instance for this effect
    var effect_label = Label.new()
    
    # Copy visual properties from UnitLabel
    effect_label.add_theme_font_size_override("font_size", 48)
    effect_label.add_theme_color_override("font_color", Color(0, 0, 0, 1))
    effect_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
    effect_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
    
    # Set text (use override if provided, otherwise copy from UnitLabel)
    if override_text != "":
        effect_label.text = override_text
    else:
        effect_label.text = unit_label.text
    
    # Position and size to match UnitLabel
    effect_label.position = unit_label.position
    effect_label.size = unit_label.size
    
    # Add to scene tree
    label_group.add_child(effect_label)
```

#### Centered Scaling Animation Pattern
**Pattern**: Scale from center using pivot_offset for symmetric growth
```gdscript
# CENTER THE SCALING: Set pivot to center of label
effect_label.pivot_offset = effect_label.size / 2.0

# Create tween for simultaneous scale + fade animation
var tween = create_tween()
tween.set_parallel(true)
tween.tween_property(effect_label, "scale", Vector2(3.0, 3.0), 1.0)
tween.tween_property(effect_label, "modulate:a", 0.0, 1.0)
```

#### Auto-Cleanup Pattern
**Pattern**: Automatic label instance removal after animation completes
```gdscript
# Cleanup: Remove the instance when animation finishes
tween.finished.connect(func():
    effect_label.queue_free()
)
```

#### Deploy Effect RPC Synchronization Pattern
**Pattern**: Single sync path for all players using local call + RPC broadcast
```gdscript
# In _deploy_army_to_territory_direct():

# Get current and calculate new unit count
var current_units = territory_node.get_unit_count()
var new_units = current_units + 1

# Call sync function locally on server (triggers effect on host)
sync_territory_units(territory_name, new_units)

# Broadcast to clients only (triggers effect on guests)
sync_territory_units.rpc(territory_name, new_units)

# RPC decorator:
@rpc("authority", "call_remote", "reliable")
func sync_territory_units(territory_name: String, new_unit_count: int):
    var old_count = territory_node.get_unit_count()
    territory_node.set_unit_count(new_unit_count)
    
    # Show effect if unit count increased during DEPLOY phase
    if new_unit_count > old_count and current_phase == GamePhase.DEPLOY:
        if territory_node.has_method("show_unit_deploy_effect"):
            territory_node.show_unit_deploy_effect()
```

#### Deploy Effect Benefits
**Benefits of Dynamic Instance Approach**:
- ✅ Multiple simultaneous effects - Player can click multiple territories rapidly, all animate independently
- ✅ No interference between deployments - Each effect is isolated in its own label instance
- ✅ Automatic cleanup - Labels removed after animation via `queue_free()`
- ✅ Text stays frozen - Each instance has snapshot of unit count at creation time
- ✅ Centered scaling - Grows symmetrically from center using pivot_offset
- ✅ All players see effects - Works for host (local call) and guests (RPC)
- ✅ Single source of truth - Same sync function handles effect for everyone

**Why This Pattern Matters**:
- **Problem**: Single static label gets reused, causing overlapping effects and missing animations
- **Solution**: Create fresh label instance per deployment, auto-cleanup after animation
- **Principle**: Dynamic instances for independent animations, single sync path for multiplayer consistency

## HUD Avatar Name & Queue Patterns

- **Hover-only name labels**
  - `HUD/player_avatar.tscn` has a `NameLabel` at the top edge of the avatar frame.
  - `NameLabel` is **hidden by default** (`visible = false`) and is only shown while the mouse hovers over the avatar root `Control`.
  - `player_avatar.gd` connects `mouse_entered` / `mouse_exited` to show/hide the label; the text is always kept up to date but only revealed on hover for low-noise UI.

- **Bot naming consistent with lobby**
  - Avatars derive bot display names from internal `bot_type` using `_get_bot_prefix_from_type(bot_type)`:
    - `Chaotic` → `"Clueless"` (Clueless Parrot)
    - `Rookie` → `"Greedy"` (Greedy Boy)
    - `Analyst` → `"Lookout"` (Lookout Pirate)
    - `Learned` → `"Captain"` (Captain Hook)
    - `Expert` → `"Flying"` (Flying Dutchman)
  - Final avatar label format for bots: `"{Prefix} {Animal}"` (e.g. `Clueless Beaver`, `Greedy Panda`) so HUD flavor matches lobby flavor.

- **Human player naming**
  - Human (Steam/local) avatars use `PlayerN` naming based on `get_player_display_id(peer_id)` from the active MP manager.
  - Fallback when data is missing is the capitalized animal name.

- **Static TurnOrder queue avatars**
  - Avatars under a parent named `TurnOrder` are **static queue avatars**:
    - They **do not** connect to `Server.turn_changed`.
    - On MP events (`player_connected`, `player_disconnected`, `display_ids_updated`) they refresh from their `represented_player_id` only.
  - Visuals (color, animal image, name, card counter) for queue avatars are driven by a single source: `represented_player_id` in `Globals.get_active_mp_manager().players`.
  - The main CURRENT_PLAYER avatar remains dynamic and continues to update on `turn_changed`.

### DEPLOY phase — human manual UNDO

- **Server** (`server.gd`): `human_deploy_undo_stack[peer_id]` is an `Array` of territory **names** (LIFO). On each manual deploy during **DEPLOY** for the **current human**, after decrementing `human_pending_armies`, append the territory name when `record_for_undo_stack` is **true**. **`_auto_deploy_for_player`** calls `_deploy_army_to_territory_direct(..., false)` so **timer** placements are **not** undoable.
- **Undo**: `_undo_last_deploy_for_peer` / `request_undo_last_deploy` — validate phase, current player, non-bot, non-empty stack, owner, **`get_unit_count() >= 2`**, then pop, `sync_territory_units` −1, increment pending, `_broadcast_pending_armies`, `_reset_phase_timer_for_new_phase`, **`_broadcast_deploy_undo_stack_depth`**.
- **Stack lifecycle**: Cleared when entering **DEPLOY** (ATTACK→DEPLOY), when advancing **DEPLOY→FORTIFY** in `advance_phase`, and at the start of **`auto_advance_deploy`** (covers client-triggered empty-pending path).
- **Clients** (`Multiplayer/multiplayer_scene.gd`): `deploy_undo_stack_depth_by_peer` updated via **`rpc_sync_deploy_undo_stack_depth`**; **`rpc_sync_pending_armies`** also sets **`local_player_avatar.set_pending_armies_absolute`** when `peer_id == multiplayer.get_unique_id()`, then refreshes **`TurnControls.update_button_state`**.
- **HUD** (`HUD/turn_controls.gd`): In DEPLOY + my turn, **UNDO** enabled when **`deploy_undo_stack_depth_by_peer[my_id] > 0`** and **`LocalPlayer.get_pending_armies() > 0`**; **`_on_deploy_pressed`** calls server undo (RPC for guest, direct for host / no-MP).

### Elimination mission — compact label + `tooltip.tscn` rules

- **`HUD/mission_display.gd`**: For `type == "elimination"`, **`description_label`** shows only **`Eliminate {Animal}.`** (`target_animal` → `capitalize()`). **`title_label`** still uses mission **`title`** from data.
- **Tooltip**: Not `Control.tooltip_text`. Scene **`HUD/mission_display.tscn`** adds **`MissionTooltipAnchor`** (full-rect **`mouse_filter = STOP`**, last child so it draws on top) with instanced **`HUD/tooltip.tscn`**. **`VBox`/labels** use **`MOUSE_FILTER_IGNORE`** so hover hits the anchor. Script stores BBCode in anchor meta **`tooltip_bbcode`** (StringName key in code); **`mouse_entered` / `mouse_exited`** call **`mission_tooltip.toggle`** like phase buttons.
- **BBCode body**: Remainder = mission **`description`** after the first **`". "`** (same as old built-in tooltip). Title line **`[b]Eliminate {Animal}:[/b]`**; body lines = **~6 words per line** via `_word_wrap_plain_lines` + newlines. **`HUD/tooltip.gd`** **`match "MissionTooltipAnchor"`** reads **`parent.get_meta(&"tooltip_bbcode", "")`**.
- **Resize**: Elimination uses **`_resize_for_standard()`** (no separate oversized elimination box).

### Game event log (MissionDisplay strip)

- **Placement & size**: `EventLog` under `MissionDisplay` in **`Multiplayer/multiplayer_scene.gd`** **`_update_avatar_scale()`**; horizontal span **`mission_width * 0.8`** (left edge aligned with mission margin).
- **Presentation**: **`HUD/event_log.gd`** + **`HUD/event_log.tscn`** — `RichTextLabel` with `bbcode_enabled`, Pirata One; rows stored as arrays of segment dicts `{ "t", "id" }` (`id` = peer or `-1` neutral); **`push_front`** per row ⇒ **newest at top**. Godot 4: set **`text`**, not `bbcode_text`.
- **Server authority**: **`server.gd`** **`_push_game_event_segments`** → **`multiplayer_scene.rpc_push_game_event_segments`** (authority, call_local). **`_push_secondary_or_immediate_segments`** appends to **`_secondary_game_events_buffer_for_combat`** when **`_buffer_secondary_game_events_for_combat_resolve`** is true (set during conquest/elimination chain inside **`_resolve_combat_on_server`**).
- **Same-tick ordering (continent / elimination vs combat)**: Log uses **`push_front`**. To show **major events newer than the combat line** (continent capture above “swept/conquered {land}”), flush with **`_push_game_event_segments(combat_segments)`** **first**, then **`for _buf in _secondary_game_events_buffer_for_combat: _push_game_event_segments(_buf)`**, then **`clear()`** the buffer. Pushing combat first puts it at index 0; each subsequent push moves continent/elimination ahead of it.
- **Clear**: **`clear_log`** when multiplayer game starts (same scene lifecycle as other HUD reset).

### Mission assign dev cheat

- **`globals.gd`**: **`CHEAT_ALWAYS_ELIMINATION_MISSION`** — when **true**, **`server.gd`** **`_assign_missions_to_players`** first tries to pick a valid **`elimination`** mission from **`available_missions`** for each **human** (not bots); falls back to existing random valid / fallback conquest if none. **Tutorial** human mission override unchanged. Toggle off for normal/release builds.

### Python `mcts_train` — offline simulator event log (Godot-adjacent, not runtime)

- **Purpose**: `mcts_train/` (repo root) trains or smoke-tests Milos rules in-process (numpy, no Godot). **Not** bundled in export.
- **`GameState` RNG**: ``rng_cards`` (deck + ``draw_from_deck`` reshuffles), ``rng_dice`` (``resolve_combat_milos``), ``rng_policy`` (bot / policy stochastic choices). ``copy()`` / ``deepcopy`` copies all three stream states.
- **`Simulator.new_game(num_players, player_names, *, mission_pool="conquest")`**: ``numpy.random.SeedSequence()`` (OS entropy) → ``spawn(5)`` → **board** (shuffle + army sprinkle), **missions** (shuffle pool from ``Missions/missions.json`` then assign seats), **cards**, **dice**, **policy**. No caller-passed seeds (each game differs unless you add a dev replay hook later).
- **`EventLog`** (`state.py`): ``enabled``, ``max_lines``, ``entries``; ``append`` no-ops when disabled; FIFO trim.
- **`Simulator`**: ``log_events``, ``max_log_lines``; ``new_game`` attaches enabled log. **``_append_log``** / **``_append_win_log``**.
- **Log line tags** (append order within one combat: combat → continent if any → elim if any):
  - **`[COMBAT]`**: territory names, players, final **dice** ``att[…] def[…]``, before/after units, ``conquered``, ``def_conq``, ``aod`` (from ``resolve_combat_milos`` 6-tuple).
  - **`[CONTINENT]`**: emitted when ``_continent_just_completed`` fires after ownership write — player name, continent name, **``+N pending deploy bonus``** from ``CONTINENT_BONUS`` (0 if continent not in table).
  - **`[ELIM]`**: ``apply_player_elimination`` — eliminated vs eliminator names, cards transferred. **Triggered from combat** when a seat’s **owned tile count hits zero** (conquest removes defender’s last land; ``def_conq`` removes attacker’s last land).
  - **`[WIN]`**: ``_append_win_log`` — reasons ``mission_complete`` (``_maybe_declare_winner`` after ``EndFortify``) or ``elimination_mission`` (instant win inside ``apply_player_elimination``). Body uses **``MissionSpec.raw``** ``title`` + ``description`` from ``Missions/missions.json``, then ``mission_id`` / ``mission_type``; fallback sentences if ``raw`` empty (``_mission_win_log_detail``).
- **Smoke**: ``scripts/smoke_rollout.py`` — ``--bots`` pattern (``1``=Rookie, ``2``=Mctsland); MCTS CLI flags; ``mcts_calibrate.py``; ``mcts_search_smoke.py``.

### Python `mcts_train` — Mctsland MCTS decisions (offline, not runtime)

- **Module** ``mcts_search.py``: defaults 100 iters / depth 5 / breadth 5. **``run_mcts_attack``** — root legal ``Combat`` arms. **``run_mcts_spree``** — post-conquest ``EndAttack`` vs continue. Truncated rollouts → ``_eval_truncated``. Coarse keys are **not** MCTS node IDs (priors + post-game table only).
- **Bot** ``mctsland_bot_player.py``:
  - **REINFORCE**: Rookie top-3 cascade consolidate to 5 units per attacker tile.
  - **DEPLOY**: one-shot — per-turn fortify UCB rank → decile 1–10 → deploy 2-tuple ``(fortify_decile, att_units)`` UCB → softmax/linear distribute; bulk ``DeployPlace``.
  - **FORTIFY**: one-shot per turn — bulk strip to hub + 6-tuple UCB distribute per cluster; bulk ``MoveUnits``.
  - **ATTACK**: attack MCTS; spree MCTS for chain; both stop/continue logged. Needs ``combat_one_round_only=False``.
  - **History**: nested ``attack`` / ``spree`` / ``deploy`` / ``fortify``; ``ensure_history_bundle`` for training; worker merge in-place.
- **Keys**: attack 7-tuple; spree 5-tuple; deploy 2-tuple ``(fortify_decile, att_units)`` max 50; fortify 6-tuple.
- **Self-play** ``scripts/mcts_selfplay.py``: writes nested JSON to ``data/``; default full-attack; worker history merge fix.