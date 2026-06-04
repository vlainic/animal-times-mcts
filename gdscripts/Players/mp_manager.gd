# Players/mp_manager.gd
extends Node

signal player_connected(peer_id: int, player_info: Dictionary)
signal player_disconnected(peer_id: int)
signal connection_failed()
signal connection_succeeded()
signal server_created()
signal local_player_updated()
signal assignment_confirmed(assignment_result: Dictionary)
signal display_ids_updated()

const DEFAULT_PORT = 25565
const MAX_CLIENTS = 6

# Predefined player options - Colorblind-friendly palette
const PLAYER_OPTIONS = {
	"beaver": Color("7A4A2EFF"),      # Darker warm brown
	"koala": Color("7E5AA6FF"),       # Deeper purple
	"llama": Color("3F8FBFFF"),       # Clearer blue
	"merkaat": Color("F3BC3BFF"),     # Yellow/Tan
	"panda": Color("5C8F3AFF"),       # Darker olive-green
	"pig": Color("E4572EFF")          # Strong orange-red
}

var peer: ENetMultiplayerPeer = null
var players = {} 
var local_player_info = {"name": "beaver", "color": PLAYER_OPTIONS["beaver"], "card_count": 0}

func _ready():
	multiplayer.peer_connected.connect(_on_player_connected)
	multiplayer.peer_disconnected.connect(_on_player_disconnected)
	multiplayer.connected_to_server.connect(_on_connected_to_server)
	multiplayer.connection_failed.connect(_on_connection_failed)

func create_server(port: int = DEFAULT_PORT):
	# print("=== SERVER CREATION DEBUG ===")
	# print("=== ENET MP: CREATE SERVER ===")
	if peer:
		# print("  WARNING: Existing peer found before host; closing it")
		peer.close()
	peer = ENetMultiplayerPeer.new()
	peer.create_server(port, MAX_CLIENTS)
	multiplayer.multiplayer_peer = peer
	players[1] = local_player_info  # Server is always peer ID 1
	# print("MPManager: Server started on port ", port)
	# print("MPManager: Server peer ID = 1")
	# print("MPManager: Server player list: ", players)
	server_created.emit()

func join_server(address: String, port: int = DEFAULT_PORT):
	# print("=== ENET MP: JOIN SERVER ===")
	if peer:
		# print("  WARNING: Existing peer found before join; closing it")
		peer.close()
	peer = ENetMultiplayerPeer.new()
	peer.create_client(address, port)
	multiplayer.multiplayer_peer = peer
	# print("Attempting to connect to ", address, ":", port)

func set_local_player(player_name: String):
	if player_name in PLAYER_OPTIONS:
		local_player_info.name = player_name
		local_player_info.color = PLAYER_OPTIONS[player_name]
		return true
	return false

func get_available_players():
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

func reset_lobby_state(_leave_remote: bool = true) -> void:
	"""Completely clear ENet multiplayer state so hosting/joining can restart cleanly."""
	# print("=== ENET MP: RESET LOBBY STATE ===")
	# print("  BEFORE: players.size() = ", players.size(), " multiplayer_peer set = ", multiplayer.multiplayer_peer != null)

	# Detach and close current peer (if any)
	if multiplayer.has_multiplayer_peer():
		multiplayer.multiplayer_peer = null
	if peer:
		peer.close()
	peer = null

	# Clear player state and reset local player to default
	players.clear()
	local_player_info = {"name": "beaver", "color": PLAYER_OPTIONS["beaver"], "card_count": 0}

	# print("  AFTER: players.size() = ", players.size(), " multiplayer_peer set = ", multiplayer.multiplayer_peer != null)
	# print("=== ENET MP: RESET LOBBY STATE DONE ===")

func validate_and_assign_animal(peer_id: int, requested_info: Dictionary) -> Dictionary:
	var requested_animal = requested_info.get("name", "")
	# print("MPManager: Validating animal request - peer:", peer_id, ", animal:", requested_animal)
	
	# Get current available animals (excluding this peer if reconnecting)
	var taken_names = []
	for existing_peer_id in players:
		if existing_peer_id != peer_id:  # Don't count self if reconnecting
			taken_names.append(players[existing_peer_id].name)
	
	# print("MPManager: Taken animals: ", taken_names)
	# print("MPManager: Requested animal available: ", requested_animal not in taken_names)
	
	# If requested animal is available, assign it
	if requested_animal in PLAYER_OPTIONS.keys() and requested_animal not in taken_names:
		var result = {
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
		# print("MPManager: Assigned requested animal: ", requested_animal)
		return result
	
	# Find first available animal
	var available_animals = []
	for animal in PLAYER_OPTIONS.keys():
		if animal not in taken_names:
			available_animals.append(animal)
	
	# print("MPManager: Available animals: ", available_animals)
	
	if available_animals.size() > 0:
		var assigned_animal = available_animals[0]
		var result = {
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
		# print("MPManager: Reassigned to available animal: ", assigned_animal)
		return result
	else:
		var result = {
			"success": false,
			"assigned_animal": "",
			"was_changed": false,
			"reason": "No animals available",
			"player_info": {}
		}
		# print("MPManager: No animals available!")
		return result

func _on_player_connected(peer_id: int):
	# print("=== PLAYER CONNECTION DEBUG ===")
	# print("Player connected: ", peer_id)
	
	if multiplayer.is_server():
		# Send current player list to the new client
		sync_all_players.rpc_id(peer_id, players)
	
	# Request player info from the new player
	request_player_info.rpc_id(peer_id)

func _on_player_disconnected(peer_id: int):
	# print("=== PLAYER DISCONNECTION DEBUG ===")
	# print("Player disconnected: ", peer_id)
	
	# If server (peer 1 from client view) disconnected and we're a client,
	# treat it as a hard failure so lobbies can reset to a clean state.
	if peer_id == 1 and not multiplayer.is_server():
		# print("=== ENET MP: Host disconnected, emitting connection_failed ===")
		players.erase(peer_id)
		connection_failed.emit()
		return  # Don't continue with normal disconnect handling - lobby will reset
	
	if peer_id in players:
		players.erase(peer_id)
		# print("Removed player from players list")
		
		# Sync updated player list to all clients
		if multiplayer.is_server():
			sync_all_players.rpc(players)
			# print("Synced updated player list to all clients")
	
	player_disconnected.emit(peer_id)

func _on_connected_to_server():
	# print("Successfully connected to server")
	connection_succeeded.emit()
	# Send our player info to the server
	send_player_info.rpc_id(1, local_player_info)

func _on_connection_failed():
	# print("Failed to connect to server")
	connection_failed.emit()

@rpc("any_peer", "call_remote", "reliable")
func request_player_info():
	var sender_id = multiplayer.get_remote_sender_id()
	send_player_info.rpc_id(sender_id, local_player_info)

@rpc("any_peer", "call_remote", "reliable")
func send_player_info(player_info: Dictionary):
	var sender_id = multiplayer.get_remote_sender_id()
	# print("=== ANIMAL ASSIGNMENT DEBUG ===")
	# print("MPManager: Received player info from peer ", sender_id, ": ", player_info)
	
	# Server validates animal selection
	if multiplayer.is_server():
		var assignment_result = validate_and_assign_animal(sender_id, player_info)
		
		# Send assignment result back to client
		confirm_animal_assignment.rpc_id(sender_id, assignment_result)
		
		# Only proceed if assignment was successful
		if assignment_result.success:
			players[sender_id] = assignment_result.player_info
			# print("MPManager: Player list now: ", players)
			player_connected.emit(sender_id, assignment_result.player_info)
			
			# Broadcast updated player list to all clients
			# print("MPManager: Broadcasting player list to all clients")
			sync_all_players.rpc(players)
		else:
			# print("MPManager: Assignment failed for player ", sender_id)
			return
	else:
		# Client-side: just store the info
		players[sender_id] = player_info
		player_connected.emit(sender_id, player_info)

@rpc("authority", "call_remote", "reliable") 
func confirm_animal_assignment(assignment_result: Dictionary):
	# print("=== ASSIGNMENT CONFIRMATION ===")
	# print("Client received assignment result: ", assignment_result)
	
	if assignment_result.success:
		# Update local player info with confirmed assignment
		local_player_info = assignment_result.player_info.duplicate()
		# print("Updated local player info to: ", local_player_info)
		
		# Emit signal for lobby to update UI
		assignment_confirmed.emit(assignment_result)
		
		if assignment_result.was_changed:
			# print("Your animal was changed: ", assignment_result.reason)
			pass
		else:
			# print("Your animal request was accepted: ", assignment_result.assigned_animal)
			pass
	else:
		# print("Animal assignment failed: ", assignment_result.reason)
		pass

@rpc("authority", "call_remote", "reliable")
func update_player_animal(new_animal: String):
	# Legacy function - kept for compatibility but prefer confirm_animal_assignment
	set_local_player(new_animal)
	local_player_updated.emit()
	# print("Server assigned you animal: ", new_animal)

@rpc("authority", "call_remote", "reliable")
func sync_all_players(all_players: Dictionary):
	# Server sends complete player list to clients
	# print("MPManager: Received player list sync: ", all_players)
	players = all_players
	
	# Notify all connected systems that player list updated
	for peer_id in players:
		player_connected.emit(peer_id, players[peer_id])

func get_players_by_peer_id() -> Array:
	# print("=== PLAYER SORTING DEBUG ===")
	# print("Current players dict: ", players)
	
	var sorted_players = []
	
	# Create array of player info with peer IDs
	for peer_id in players:
		var player_info = players[peer_id].duplicate()
		
		var player_data = {
			"peer_id": peer_id,
			"name": player_info.name,
			"color": player_info.color
		}
		sorted_players.append(player_data)
	
	# Sort by peer_id for consistent round-robin distribution
	sorted_players.sort_custom(func(a, b): return a.peer_id < b.peer_id)
	
	# print("Sorted players by peer ID: ", sorted_players)
	return sorted_players

func notify_display_ids_updated():
	"""Notify listeners that display IDs or related data has been updated"""
	display_ids_updated.emit()
