# Players/player.gd
extends Node2D
class_name Player

var peer_id: int
var player_name: String
var player_color: Color
var card_positions: Array[Vector2] = []
var card_sizes: Array[Vector2] = []
var assigned_mission: Dictionary = {}

func initialize(input_peer_id: int, player_info: Dictionary):
	self.peer_id = input_peer_id
	self.player_name = player_info.get("name", "unknown")
	self.player_color = player_info.get("color", Color.WHITE)
	
	# Set node name for easy identification
	name = "Player" + str(peer_id)
	
	# # print("Player initialized: ", player_name, " (Peer: ", peer_id, ")")

func _ready():
	# Set metadata after node is ready
	var metadata = $MetaData
	if metadata:
		metadata.set_meta("player_id", peer_id)
		metadata.set_meta("player_color", player_color)
	
	# Load hand display scene and extract placeholder positions and sizes
	_load_card_layout()

func get_player_info() -> Dictionary:
	return {
		"name": player_name,
		"color": player_color,
		"peer_id": peer_id
	}

func get_cards_array() -> Array:
	"""Get array of all card instances for hand display"""
	var cards_node = get_node_or_null("Cards")
	if cards_node:
		var cards = []
		for i in range(cards_node.get_child_count()):
			cards.append(cards_node.get_child(i))
		return cards
	return []

func set_mission(mission_data: Dictionary):
	"""Set the assigned mission for this player"""
	assigned_mission = mission_data
	# print("Player ", player_name, " assigned mission: ", mission_data.get("title", "Unknown"))
	# print("Player ", player_name, " assigned mission: ", mission_data)

func get_mission() -> Dictionary:
	"""Get the assigned mission for this player"""
	return assigned_mission

func _load_card_layout():
	"""Load card layout from existing hand_display instance"""
	# Find hand_display in the scene tree
	var hand_display = get_tree().get_first_node_in_group("hand_display") 
	
	if not hand_display:
		# Fallback: search common locations
		var main_scene = get_tree().get_first_node_in_group("main_scene")
		if main_scene:
			hand_display = main_scene.get_node_or_null("HandDisplay")
	
	if not hand_display:
		print("Player ", player_name, ": Warning - Could not find hand_display")
		return
	
	# Wait for hand_display to finish async initialization
	# _initialize_placeholders() is async and takes multiple frames
	var max_wait_frames = 10
	var frames_waited = 0
	while frames_waited < max_wait_frames:
		var layout_info = hand_display.get_layout_info()
		if not layout_info.is_empty():
			break
		await get_tree().process_frame
		frames_waited += 1
	
	# Calculate improved positions based on hand_display container
	_calculate_improved_layout(hand_display, 5)  # Default to 5 for initial load
	
	# #print("Player ", player_name, ": Calculated ", card_positions.size(), " improved card layouts from hand_display")

func _calculate_improved_layout(hand_display, _actual_card_count: int):
	"""Calculate better card positions using hand_display's layout system for all 9 cards"""
	card_positions.clear()
	card_sizes.clear()
	
	# Use hand_display's built-in layout system that handles both containers
	var layout_info = hand_display.get_layout_info()
	if layout_info.is_empty():
		print("Player ", player_name, ": Warning - No layout info from hand_display")
		return
	
	# Get all positions from hand_display (handles both CardContainer and CardContainer2)
	var max_cards = hand_display.get_max_cards()
	# print("Player ", player_name, ": Using hand_display layout for", max_cards, "card positions")
	
	# Use the hand_display's calculated positions for all cards
	for i in range(max_cards):
		if i in layout_info:
			var card_info = layout_info[i]
			card_positions.append(card_info.position)
			card_sizes.append(card_info.size)
		else:
			# Fallback if layout info is incomplete
			card_positions.append(Vector2.ZERO)
			card_sizes.append(Vector2.ZERO)
	
	# #print("Player ", player_name, ": Loaded", card_positions.size(), "card positions from hand_display")



func position_cards_in_hand():
	"""Position all cards in hand using loaded card layout"""
	# print("=== CARD POSITIONING DEBUG - POSITION CARDS ===")
	# print("Player:", player_name, "position_cards_in_hand() called")
	
	# PRIVACY: Only show cards for the local player
	var local_peer_id = multiplayer.get_unique_id()
	# print("Local peer ID:", local_peer_id, "Player peer ID:", peer_id)
	
	var cards_node = get_node_or_null("Cards")
	if peer_id != local_peer_id:
		# print("This is not the local player - hiding all cards")
		# This is not the local player - hide all their cards
		if cards_node:
			for card in cards_node.get_children():
				# print("  Hiding card:", card.name, " at position:", card.position)
				card.visible = false
		return
	
	if not cards_node:
		print("ERROR: Player", player_name, ": No Cards node found")
		return
	
	var card_count = cards_node.get_child_count()
	# print("Player", player_name, ": Positioning", card_count, "cards in hand")
	
	# # DEBUG: Check scene tree structure
	# print("=== SCENE TREE DEBUG ===")
	# var main_scene = get_tree().current_scene
	# print("Current scene:", main_scene.name if main_scene else "null")
	
	# # DEBUG: Check all nodes in hand_display group
	# var hand_display_nodes = get_tree().get_nodes_in_group("hand_display")
	# print("Nodes in hand_display group:", hand_display_nodes.size())
	# for i in range(hand_display_nodes.size()):
	# 	print("  Group node", i, ":", hand_display_nodes[i].name, "at path:", hand_display_nodes[i].get_path())
	
	# # DEBUG: Try alternative detection methods
	# var hand_display_by_path = main_scene.get_node_or_null("HandDisplay")
	# print("HandDisplay by path:", hand_display_by_path != null, "at path:", hand_display_by_path.get_path() if hand_display_by_path else "null")
	
	# Recalculate layout for current card count
	var hand_display = get_tree().get_first_node_in_group("hand_display")
	# print("Hand display found by group:", hand_display != null)
	# if hand_display:
	# 	print("Hand display path:", hand_display.get_path())
	# 	print("Hand display ready:", hand_display._is_hand_ready() if hand_display.has_method("_is_hand_ready") else "no _is_hand_ready method")
	# 	print("Hand display max cards:", hand_display.get_max_cards() if hand_display.has_method("get_max_cards") else "no get_max_cards method")
	
	if hand_display and card_count > 0:
		# print("Recalculating layout for", card_count, "cards")
		_calculate_improved_layout(hand_display, card_count)
	elif not hand_display:
		print("WARNING: No hand_display found for player ", player_name)
	elif card_count == 0:
		# No cards to position - this is normal, not a warning
		pass
	
	var max_cards = card_positions.size()
	# print("Max card positions available:", max_cards)
	
	# Position each card using loaded layout arrays
	# print("Positioning cards...")
	for i in range(card_count):
		if i < max_cards:
			var card_instance = cards_node.get_child(i)
			
			# Setup card visuals via hand_display
			if hand_display and hand_display.has_method("setup_card_for_display"):
				hand_display.setup_card_for_display(card_instance)
			
			var card_position = card_positions[i]
			var card_size = card_sizes[i]
			
			# print("Card", i+1, ":", card_instance.name, " - Position:", card_position, " Size:", card_size)
			# print("  Card type:", card_instance.get_class())
			# print("  Current position before move:", card_instance.position)
			
			# Move card to hand display position (handle different node types)
			if card_instance is Control or card_instance is Node2D:
				card_instance.position = card_position
				# print("  -> Moved to position:", card_position)
				
			if card_instance is Control:
				card_instance.size = card_size
				# print("  -> Control size set to:", card_instance.size)
			elif card_instance is Node2D:
				# For Node2D, use scale instead of size
				# Assume base card size and calculate scale ratio
				var base_size = Vector2(100, 150)  # Default card size
				var scale_ratio = Vector2(card_size.x / base_size.x, card_size.y / base_size.y)
				card_instance.scale = scale_ratio
				# print("  -> Node2D scale set to:", card_instance.scale)
				# print("  -> Positioned", card_instance.get_class(), "card", i+1, "at", card_position)
			else:
				# For Node type cards, find the visual child node to position
				print("  -> Handling Node type card, looking for CardBackground...")
				var visual_child = card_instance.get_node_or_null("CardBackground")
				if visual_child and (visual_child is Control or visual_child is Node2D):
					visual_child.position = card_position
					print("  -> Moved visual child to position:", card_position)
				if visual_child is Control:
					visual_child.size = card_size
					print("  -> Control visual child size set to:", card_size)
				elif visual_child is Node2D:
					# For Node2D, use scale instead of size
					var base_size = Vector2(100, 150)  # Default card size
					var scale_ratio = Vector2(card_size.x / base_size.x, card_size.y / base_size.y)
					visual_child.scale = scale_ratio
					print("  -> Node2D visual child scale set to:", scale_ratio)
					print("  -> Positioned card", i+1, "visual child at", card_position)
				else:
					print("  -> WARNING: Cannot position card", i+1, "(unsupported node type)")
		else:
			print("  -> WARNING: More cards than hand display can show!")

func notify_card_added():
	"""Called when a card is added to this player"""
	# print("=== CARD POSITIONING DEBUG - NOTIFY CARD ADDED ===")
	# print("Player:", player_name, "notify_card_added() called")
	
	var cards_node = get_node_or_null("Cards")
	if cards_node:
		var _card_count = cards_node.get_child_count()
		# print("Player", player_name, ": Card added, now have", _card_count, "cards")
		
		# Position cards in hand using hand display layout
		# print("Calling position_cards_in_hand()...")
		position_cards_in_hand()
		# print("position_cards_in_hand() completed")
	else:
		print("ERROR: Player", player_name, ": No Cards node found for card notification")

func remove_cards_by_type(cards_to_remove: Array) -> Dictionary:
	"""Nuclear approach: Rebuild Cards node from scratch after removing traded cards
	Returns dictionary with 'paths' (Array) and 'instances' (Array) of removed cards"""
	var cards_node = get_node_or_null("Cards")
	if not cards_node:
		print("Player ", player_name, ": No Cards node found for card removal")
		return {"paths": [], "instances": []}
	
	# Step 1: Inventory all current cards
	var current_inventory = []
	for i in range(cards_node.get_child_count()):
		var card_instance = cards_node.get_child(i)
		
		# Get card_path from metadata (single source of truth)
		var scene_path = card_instance.get_meta("card_path", "")
		
		# Get card_type from metadata (single source of truth)
		var card_type = card_instance.get_meta("card_type", "")
		
		# Fallback: if metadata missing, try scene_file_path (for old cards)
		if scene_path.is_empty():
			scene_path = card_instance.scene_file_path
			if not scene_path.is_empty():
				# Extract type from path as fallback
				var path_parts = scene_path.split("/")
				var filename = path_parts[-1]
				var filename_parts = filename.split(".")
				card_type = filename_parts[0]
		
		current_inventory.append({
			"type": card_type,
			"path": scene_path,
			"instance": card_instance
		})
	
	# Step 2: Calculate what to remove (for depot tracking)
	var removed_paths = []
	var removed_instances = []
	var remaining_inventory = current_inventory.duplicate()
	
	for card_type_to_remove in cards_to_remove:
		# Find and mark for removal
		for i in range(remaining_inventory.size() - 1, -1, -1):
			if remaining_inventory[i]["type"] == card_type_to_remove:
				var removed_card = remaining_inventory[i]
				removed_paths.append(removed_card["path"])
				removed_instances.append(removed_card["instance"])
				remaining_inventory.remove_at(i)
				break
	
	# Step 3: NUCLEAR CLEAR - Remove ALL cards from Cards node
	# # print("Player ", player_name, ": NUCLEAR CLEAR - Removing all cards from node")
	for card_data in current_inventory:
		var card_instance = card_data["instance"]
		cards_node.remove_child(card_instance)
		# Don't queue_free() removed cards yet - let hand_display handle them
		if card_instance in removed_instances:
			# Keep removed instances alive for hand_display
			pass
		else:
			card_instance.queue_free()
	
	# # print("Player ", player_name, ": Cards after nuclear clear: ", cards_node.get_child_count())
	
	# Step 4: REBUILD - Re-instantiate remaining cards fresh
	# # print("Player ", player_name, ": REBUILDING - Re-instantiating ", remaining_inventory.size(), " remaining cards")
	var server = get_node_or_null("/root/Server")
	for card_data in remaining_inventory:
		var scene_path = card_data["path"]
		if not scene_path.is_empty():
			# Load treasure.tscn template
			var card_scene = preload("res://Cards/treasure.tscn")
			var new_card_instance = card_scene.instantiate()
			
			# Set metadata (single source of truth)
			new_card_instance.set_meta("card_path", scene_path)
			if server:
				new_card_instance.set_meta("card_type", server._extract_card_type_from_path(scene_path))
				new_card_instance.set_meta("territory_name", server._extract_territory_name_from_path(scene_path))
			else:
				# Fallback: extract manually if Server not available
				var filename = scene_path.get_file().get_basename().to_lower()
				new_card_instance.set_meta("card_type", filename)
				if "treasure.tscn" in scene_path:
					new_card_instance.set_meta("territory_name", "SHIP")
				else:
					var parts = scene_path.split("/")
					if parts.size() >= 2:
						new_card_instance.set_meta("territory_name", parts[parts.size() - 2])
			
			cards_node.add_child(new_card_instance)
			# # print("Player ", player_name, ": Rebuilt card: ", card_data["type"], " from ", scene_path)
	
	# # print("Player ", player_name, ": Cards after rebuild: ", cards_node.get_child_count())
	# # print("Player ", player_name, ": Removed ", removed_paths.size(), " cards, paths: ", removed_paths)
	
	# Step 5: Position the fresh cards
	position_cards_in_hand()
	
	# Update coin labels on all territories after cards are removed
	var map_node = get_node_or_null("/root/MultiplayerScene/Map")
	if map_node and map_node.has_method("update_all_coin_labels"):
		map_node.update_all_coin_labels()
	
	return {"paths": removed_paths, "instances": removed_instances} 
