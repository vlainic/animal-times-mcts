# Players/steam_mp_manager.gd
# Steam-specific multiplayer manager - mirrors mp_manager.gd API
extends Node

# Re-emit the same signals as MultiplayerManager for compatibility
signal player_connected(peer_id: int, player_info: Dictionary)
signal player_disconnected(peer_id: int)
signal connection_failed()
signal connection_succeeded()
signal server_created()
signal local_player_updated()
signal assignment_confirmed(assignment_result: Dictionary)
signal lobbies_updated(lobbies: Array)
signal display_ids_updated()

# Use same constants as MultiplayerManager
const MAX_CLIENTS = 6
const PLAYER_OPTIONS = {
	"beaver": Color("7A4A2EFF"),
	"koala": Color("7E5AA6FF"),
	"llama": Color("3F8FBFFF"),
	"merkaat": Color("F3BC3BFF"),
	"panda": Color("5C8F3AFF"),
	"pig": Color("E4572EFF")
}

# Steam-specific state
var peer: SteamMultiplayerPeer = null
var lobby_id: int = 0
var lobby_members: Array = []
var is_host: bool = false
# Track original lobby owner so we can detect host leaving in pre-game
var host_steam_id: int = 0

# Player state (same as MultiplayerManager)
var players: Dictionary = {}
var local_player_info: Dictionary = {"name": "", "color": Color.WHITE, "card_count": 0}

func _ready():
	# Connect Steam lobby callbacks whenever Steam is initialized (so create_server/join_server get callbacks)
	if not SteamManager.is_steam_initialized:
		return
	Steam.lobby_created.connect(_on_lobby_created)
	Steam.lobby_joined.connect(_on_lobby_joined)
	Steam.lobby_match_list.connect(_on_lobby_list_received)
	Steam.p2p_session_request.connect(_on_p2p_session_request)
	if Steam.has_method("lobby_chat_update"):
		Steam.lobby_chat_update.connect(_on_lobby_chat_update)

	# Multiplayer peer signals only when in Steam MP mode (set when entering steam_lobby)
	if Globals.USE_STEAM_MULTIPLAYER:
		_connect_multiplayer_signals()

func ensure_multiplayer_signals_connected():
	"""Connect multiplayer peer signals when entering Steam MP (e.g. from steam_lobby). Idempotent."""
	_connect_multiplayer_signals()

func _connect_multiplayer_signals():
	if multiplayer.peer_connected.is_connected(_on_player_connected):
		return
	multiplayer.peer_connected.connect(_on_player_connected)
	multiplayer.peer_disconnected.connect(_on_player_disconnected)
	multiplayer.connected_to_server.connect(_on_connected_to_server)
	multiplayer.connection_failed.connect(_on_connection_failed)

# ============================================================================
# PUBLIC API (mirrors MultiplayerManager)
# ============================================================================

func create_server():
	"""Create a Steam lobby (host)"""
	print("=== CREATING STEAM LOBBY ===")
	print("  Current lobby_id: ", lobby_id)

	if lobby_id != 0:
		push_warning("Already in a lobby! lobby_id = %d" % lobby_id)
		print("  ERROR: Already in lobby, aborting create_server")
		return

	print("  Calling Steam.createLobby with LOBBY_TYPE_PUBLIC, MAX_CLIENTS = ", MAX_CLIENTS)
	Steam.createLobby(Steam.LOBBY_TYPE_PUBLIC, MAX_CLIENTS)
	print("  Steam.createLobby() called successfully, waiting for callback...")
	# Callback: _on_lobby_created will be called

func join_server(lobby_id_to_join: int):
	"""Join a Steam lobby by ID"""
	print("=== JOINING STEAM LOBBY: ", lobby_id_to_join, " ===")
	Steam.joinLobby(lobby_id_to_join)
	# Callback: _on_lobby_joined will be called

func request_lobby_list():
	"""Request list of available lobbies"""
	print("=== REQUESTING LOBBY LIST ===")

	# Add distance filter (worldwide)
	Steam.addRequestLobbyListDistanceFilter(Steam.LOBBY_DISTANCE_FILTER_WORLDWIDE)
	Steam.requestLobbyList()
	# Callback: _on_lobby_list_received will be called

func set_local_player(player_name: String) -> bool:
	"""Same as MultiplayerManager.set_local_player"""
	if player_name in PLAYER_OPTIONS:
		local_player_info.name = player_name
		local_player_info.color = PLAYER_OPTIONS[player_name]
		return true
	return false

func get_available_players() -> Array:
	"""Same as MultiplayerManager.get_available_players"""
	var taken_names = []
	for peer_id in players:
		taken_names.append(players[peer_id].name)

	var available = []
	for animal_name in PLAYER_OPTIONS.keys():
		if animal_name not in taken_names:
			available.append(animal_name)
	return available

func get_player_display_id(peer_id: int) -> int:
	return peer_id

func notify_display_ids_updated():
	"""Notify listeners that display IDs or related data has been updated"""
	display_ids_updated.emit()

func reset_lobby_state(leave_remote: bool = true) -> void:
	"""Completely clear Steam lobby/multiplayer state so hosting/joining can restart cleanly."""
	if leave_remote and lobby_id != 0:
		Steam.leaveLobby(lobby_id)
	if multiplayer.has_multiplayer_peer():
		multiplayer.multiplayer_peer = null
	peer = null
	lobby_id = 0
	lobby_members.clear()
	is_host = false
	host_steam_id = 0
	players.clear()
	local_player_info = {"name": "", "color": Color.WHITE, "card_count": 0}

# ============================================================================
# STEAM LOBBY CALLBACKS
# ============================================================================

func _on_lobby_created(result: int, created_lobby_id: int):
	"""Called when Steam creates our lobby"""
	print("=== LOBBY CREATED CALLBACK ===")
	print("  Result: ", result, " (1 = success, other = error)")
	print("  Lobby ID: ", created_lobby_id)

	if result != 1:  # 1 = k_EResultOK (success for lobby_created)
		push_error("Failed to create lobby with result code: %d" % result)
		print("  ERROR: Lobby creation failed, emitting connection_failed signal")
		connection_failed.emit()
		return
	
	print("  Lobby created successfully!")

	# Store lobby info
	lobby_id = created_lobby_id
	is_host = true
	host_steam_id = SteamManager.steam_id

	# Set lobby data (name, metadata)
	print("  Setting lobby data...")
	Steam.setLobbyData(lobby_id, "name", "AnimalTimes_" + SteamManager.steam_username)
	Steam.setLobbyData(lobby_id, "mode", "co-op")
	Steam.setLobbyJoinable(lobby_id, true)
	print("  Lobby data set successfully")

	# Init relay network access before creating host (required on some platforms)
	print("  Steam running: ", Steam.isSteamRunning())
	if Steam.has_method("initRelayNetworkAccess"):
		Steam.initRelayNetworkAccess()
		print("  initRelayNetworkAccess() called")
	else:
		print("  WARNING: initRelayNetworkAccess not available")

	# Create the Steam multiplayer peer as HOST
	print("  Creating SteamMultiplayerPeer as host...")
	peer = SteamMultiplayerPeer.new()
	peer.server_relay = true  # Enable relay for NAT traversal

	var error = peer.create_host(0)  # Virtual port 0
	print("  create_host() returned: ", error, " = ", error_string(error))
	if error != OK:
		push_error("Failed to create Steam host with error code: %d" % error)
		print("  ERROR: create_host failed, cleaning up lobby and emitting connection_failed")
		Steam.leaveLobby(lobby_id)
		lobby_id = 0
		is_host = false
		connection_failed.emit()
		return

	# Set as the active multiplayer peer
	print("  Setting multiplayer.multiplayer_peer...")
	multiplayer.multiplayer_peer = peer

	print("  Steam lobby created and host ready!")
	print("  Emitting server_created signal...")
	server_created.emit()

func _on_lobby_joined(lobby_id_joined: int, _permissions: int, _locked: bool, response: int):
	"""Called when we join a lobby (either as host after creation, or as client)"""
	print("=== LOBBY JOINED CALLBACK ===")
	print("Lobby ID: ", lobby_id_joined)
	print("Response: ", response, " (1 = success)")

	if response != 1:  # 1 = CHAT_ROOM_ENTER_RESPONSE_SUCCESS (lobby join uses 1 for success)
		push_error("Failed to join lobby: ", response)
		connection_failed.emit()
		return

	lobby_id = lobby_id_joined

	# Get lobby owner
	var owner_id = Steam.getLobbyOwner(lobby_id)

	# If we're the owner, we already set up host in _on_lobby_created
	if owner_id == SteamManager.steam_id:
		print("We are the lobby owner (host)")
		host_steam_id = owner_id
		return

	# We're a client - connect to the host
	print("We are a client, connecting to host: ", owner_id)
	host_steam_id = owner_id

	peer = SteamMultiplayerPeer.new()
	peer.server_relay = true
	var error = peer.create_client(owner_id, 0)  # Connect to owner, virtual port 0
	if error != OK:
		push_error("Failed to create Steam client: ", error)
		connection_failed.emit()
		return

	multiplayer.multiplayer_peer = peer
	print("Steam client peer created, waiting for connection...")

func _on_lobby_list_received(lobbies: Array):
	"""Called when Steam returns list of available lobbies"""
	print("=== LOBBY LIST RECEIVED ===")
	print("Found ", lobbies.size(), " lobbies")

	# Parse lobby data
	var lobby_list = []
	for lobby in lobbies:
		var lobby_name = Steam.getLobbyData(lobby, "name")
		var lobby_mode = Steam.getLobbyData(lobby, "mode")
		var member_count = Steam.getNumLobbyMembers(lobby)

		# Optional: Filter empty names (common in test app 480)
		if lobby_name == "":
			continue

		lobby_list.append({
			"id": lobby,
			"name": lobby_name,
			"mode": lobby_mode,
			"members": member_count,
			"max_members": MAX_CLIENTS
		})

		print("  - %s [%d/%d] (%s)" % [lobby_name, member_count, MAX_CLIENTS, lobby_mode])

	lobbies_updated.emit(lobby_list)

func _on_p2p_session_request(remote_id: int):
	"""Called when another peer wants to establish P2P connection"""
	print("=== P2P SESSION REQUEST from ", remote_id, " ===")
	var requester_name = Steam.getFriendPersonaName(remote_id)
	print("Requester: ", requester_name)

	# Accept the session
	Steam.acceptP2PSessionWithUser(remote_id)

func _on_lobby_chat_update(updated_lobby_id: int, _changed_id: int, _making_change_id: int, _chat_state: int):
	"""Detect when lobby membership changes (e.g. host leaves before game start)."""
	# Only care about the current lobby and when we're a client (not host/server)
	if updated_lobby_id != lobby_id:
		return
	if multiplayer.is_server():
		return
	if lobby_id == 0:
		return
	
	# Query current lobby owner; if it no longer matches the original host,
	# treat it as the lobby/host being gone and emit connection_failed so
	# steam_lobby can reset to a clean pre-lobby state.
	var current_owner_id = Steam.getLobbyOwner(lobby_id)
	if host_steam_id != 0 and current_owner_id != host_steam_id:
		print("=== STEAM: Lobby owner changed or host left before game start - emitting connection_failed ===")
		connection_failed.emit()

# ============================================================================
# MULTIPLAYER SIGNAL HANDLERS (same as mp_manager.gd)
# ============================================================================

func _on_player_connected(peer_id: int):
	print("=== STEAM: PLAYER CONNECTED: ", peer_id, " ===")
	print("  Am I server? ", multiplayer.is_server())
	print("  My peer ID: ", multiplayer.get_unique_id())

	if multiplayer.is_server():
		# Server: Send current player list to new client
		print("  Server: Sending current players to peer ", peer_id)
		sync_all_players.rpc_id(peer_id, players)
		# Don't request info - client will send when they pick an animal
	# Clients do nothing on player_connected

func _on_player_disconnected(peer_id: int):
	print("=== STEAM: PLAYER DISCONNECTED: ", peer_id, " ===")
	print("  Am I server? ", multiplayer.is_server())

	# If server (peer 1 from client view) disconnected and we're a client
	if peer_id == 1 and not multiplayer.is_server():
		print("  ERROR: Host disconnected! Lobby is dead.")
		connection_failed.emit()

	if peer_id in players:
		players.erase(peer_id)

		if multiplayer.is_server():
			sync_all_players.rpc(players)

	player_disconnected.emit(peer_id)

func _on_connected_to_server():
	print("=== STEAM: Successfully connected to server ===")
	connection_succeeded.emit()

func _on_connection_failed():
	print("=== STEAM: Failed to connect to server ===")
	connection_failed.emit()

# ============================================================================
# RPCs (adapted from mp_manager.gd)
# ============================================================================

@rpc("any_peer", "call_remote", "reliable")
func request_player_info():
	var sender_id = multiplayer.get_remote_sender_id()
	# Only send if we've picked an animal
	if local_player_info.name != "":
		send_player_info.rpc_id(sender_id, local_player_info)

@rpc("any_peer", "call_remote", "reliable")
func send_player_info(player_info: Dictionary):
	var sender_id = multiplayer.get_remote_sender_id()

	if multiplayer.is_server():
		var assignment_result = validate_and_assign_animal(sender_id, player_info)
		confirm_animal_assignment.rpc_id(sender_id, assignment_result)

		if assignment_result.success:
			players[sender_id] = assignment_result.player_info
			player_connected.emit(sender_id, assignment_result.player_info)
			sync_all_players.rpc(players)
		else:
			return
	else:
		players[sender_id] = player_info
		player_connected.emit(sender_id, player_info)

@rpc("authority", "call_remote", "reliable")
func confirm_animal_assignment(assignment_result: Dictionary):
	if assignment_result.success:
		local_player_info = assignment_result.player_info.duplicate()
		assignment_confirmed.emit(assignment_result)

@rpc("authority", "call_remote", "reliable")
func sync_all_players(all_players: Dictionary):
	players = all_players
	for peer_id in players:
		player_connected.emit(peer_id, players[peer_id])

func validate_and_assign_animal(peer_id: int, requested_info: Dictionary) -> Dictionary:
	"""Validate and assign animal - copied from mp_manager.gd"""
	var requested_animal = requested_info.get("name", "")

	var taken_names = []
	for existing_peer_id in players:
		if existing_peer_id != peer_id:
			taken_names.append(players[existing_peer_id].name)

	if requested_animal in PLAYER_OPTIONS.keys() and requested_animal not in taken_names:
		return {
			"success": true,
			"assigned_animal": requested_animal,
			"was_changed": false,
			"reason": "Animal assigned as requested",
			"player_info": {
				"name": requested_animal,
				"color": PLAYER_OPTIONS[requested_animal],
				"card_count": 0
			}
		}

	var available_animals = []
	for animal in PLAYER_OPTIONS.keys():
		if animal not in taken_names:
			available_animals.append(animal)

	if available_animals.size() > 0:
		var assigned_animal = available_animals[0]
		return {
			"success": true,
			"assigned_animal": assigned_animal,
			"was_changed": true,
			"reason": "Requested animal '" + requested_animal + "' was taken. Assigned '" + assigned_animal + "' instead.",
			"player_info": {
				"name": assigned_animal,
				"color": PLAYER_OPTIONS[assigned_animal],
				"card_count": 0
			}
		}
	else:
		return {
			"success": false,
			"assigned_animal": "",
			"was_changed": false,
			"reason": "No animals available",
			"player_info": {}
		}

func get_players_by_peer_id() -> Array:
	"""Get sorted players list - copied from mp_manager.gd"""
	var sorted_players = []

	for peer_id in players:
		var player_info = players[peer_id].duplicate()
		var player_data = {
			"peer_id": peer_id,
			"name": player_info.name,
			"color": player_info.color
		}
		sorted_players.append(player_data)

	sorted_players.sort_custom(func(a, b): return a.peer_id < b.peer_id)
	return sorted_players
