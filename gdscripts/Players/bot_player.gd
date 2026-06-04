class_name BotPlayer
extends Player

# Bot-specific properties
var bot_type: String = "Chaotic"
var territory_matrix: Array = []
var territory_indexing: Dictionary = {}  # name -> index
var index_mapping: Dictionary = {}       # index -> name (string keys)
var owned_territory_indexes: Array = []  # Pre-calculated owned territory indexes

# Override Player initialization for bot-specific setup
func initialize_bot(bot_peer_id: int, player_info: Dictionary, bot_type_name: String):
	self.bot_type = bot_type_name
	initialize(bot_peer_id, player_info)  # Call parent Player.initialize()
	_load_territory_data()
	# Note: _calculate_owned_territory_indexes() will be called after territory assignment

func calculate_owned_territories():
	"""Public function to calculate owned territories after territory assignment"""
	_calculate_owned_territory_indexes()

func _load_territory_data():
	# Load territory matrix and indexing for O(1) lookups
	var matrix_file = FileAccess.open("res://Python/territory_matrix.json", FileAccess.READ)
	if matrix_file:
		var json_string = matrix_file.get_as_text()
		matrix_file.close()
		var json = JSON.new()
		var parse_result = json.parse(json_string)
		if parse_result == OK:
			territory_matrix = json.data
			# print("BotPlayer: Loaded territory matrix with ", territory_matrix.size(), " territories")
	
	# Load territory_indexing.json (name -> index)
	var indexing_file = FileAccess.open("res://Map/Territories/territory_indexing.json", FileAccess.READ)
	if indexing_file:
		var json_string = indexing_file.get_as_text()
		indexing_file.close()
		var json = JSON.new()
		var parse_result = json.parse(json_string)
		if parse_result == OK:
			territory_indexing = json.data
			# print("BotPlayer: Loaded territory indexing with ", territory_indexing.size(), " territories")
	
	# Load index_mapping.json (index -> name, string keys)
	var mapping_file = FileAccess.open("res://Map/Territories/index_mapping.json", FileAccess.READ)
	if mapping_file:
		var json_string = mapping_file.get_as_text()
		mapping_file.close()
		var json = JSON.new()
		var parse_result = json.parse(json_string)
		if parse_result == OK:
			index_mapping = json.data
			# print("BotPlayer: Loaded index mapping with ", index_mapping.size(), " territories")

func _calculate_owned_territory_indexes():
	"""Calculate and store which territory indexes this bot owns with smart filtering"""
	owned_territory_indexes.clear()
	
	# print("=== BOT ", player_name, " TERRITORY CALCULATION ===")
	# print("Bot ", player_name, ": Calculating owned territory indexes...")
	
	if not Server or not Server.map:
		# print("Bot ", player_name, ": ERROR - No Server or Server.map available")
		return
	
	var _total_territories_checked = 0
	var all_owned_territories = []
	
	# First, collect all owned territories
	for continent in Server.map.get_children():
		if continent.name in ["Mudflats", "Bamboovia", "Riverside", "Peaks", "Bushlands", "Eucalypta"]:
			for territory in continent.get_children():
				_total_territories_checked += 1
				
				if territory.has_method("get_owner_id"):
					var owner_id = territory.get_owner_id()
					var territory_index = territory.get_node("MetaData").get_meta("territory_index", -1)
					
					if owner_id == peer_id:
						all_owned_territories.append(territory_index)
				else:
					# print("Bot ", player_name, ": ERROR - Territory ", territory.name, " has no get_owner_id method")
					pass

	owned_territory_indexes = all_owned_territories


func _is_legal_attacker(territory_index: int) -> bool:
	# Smart filtering based on Attack of Despair mode
	if Globals.ATTACK_OF_DESPAIR:
		# Attack of Despair: include ALL owned territories (even 1-unit ones)
		return true
	else:
		# Normal mode: only include territories with >1 unit
		return _territory_has_sufficient_units(territory_index)
		

func _territory_has_sufficient_units(territory_index: int) -> bool:
	"""Check if territory has more than 1 unit (sufficient for normal attacks)"""
	var territory_name = _get_territory_name_by_index(territory_index)
	if territory_name.is_empty():
		# print("Bot ", player_name, ": ERROR - Empty territory name for index ", territory_index)
		return false
	
	# Convert to underscore format for map lookup
	var territory_name_underscore = territory_name.replace(" ", "_")
	# print("Bot ", player_name, ": Checking territory: ", territory_name, " -> ", territory_name_underscore)
	
	var territory_node = Server.map.find_territory(territory_name_underscore)
	if not territory_node:
		# print("Bot ", player_name, ": ERROR - Territory not found: ", territory_name_underscore)
		return false
	
	var units = territory_node.get_node("MetaData").get_meta("unit_count", 0)
	var has_sufficient = units > 1
	# print("Bot ", player_name, ": Territory found: ", territory_name_underscore, ", units: ", units, ", sufficient: ", has_sufficient)
	return has_sufficient

func print_owned_territories():
	"""Print current owned territories for debugging"""
	# print("Bot ", player_name, ": Currently owns ", owned_territory_indexes.size(), " territories:")
	for index in owned_territory_indexes:
		var _territory_name = _get_territory_name_by_index(index)
		# print("  - ", _territory_name, " (index: ", index, ")")

# Abstract phase handlers - to be overridden by specific bot types
func handle_reinforce_phase():
	pass  # Override in subclasses

func handle_attack_phase():
	pass  # Override in subclasses

func handle_deploy_phase():
	pass  # Override in subclasses

func handle_fortify_phase():
	pass  # Override in subclasses


func _get_neighbors_by_index(territory_index: int) -> Array:
	var neighbors = []
	if territory_index < territory_matrix.size():
		for i in range(territory_matrix[territory_index].size()):
			if territory_matrix[territory_index][i] == 1:
				neighbors.append(i)
	return neighbors

func _get_territory_name_by_index(index: int) -> String:
	# Use index_mapping.json with string keys
	var index_key = str(index)
	if index_mapping.has(index_key):
		return index_mapping[index_key]
		# print("Bot ", player_name, ": ERROR - No territory found for index ", index)
	return ""

func _get_territory_index_by_name(territory_name: String) -> int:
	"""Get territory index by name using territory_indexing.json data"""
	if territory_indexing.has(territory_name):
		return territory_indexing[territory_name]
	# print("Bot ", player_name, ": ERROR - No territory index found for name: ", territory_name)
	return -1

# === SERVER-SIDE CARD HANDLING METHODS ===
func add_card_directly(_card_path: String):
	"""Bots don't need visual cards - skip card addition"""
	# print("BotPlayer: Skipping card addition for bot ", player_name, " (no visual cards needed)")
	pass

func remove_cards_directly(_cards_to_remove: Array):
	"""Bots don't need visual cards - skip card removal"""
	# print("BotPlayer: Skipping card removal for bot ", player_name, " (no visual cards needed)")
	pass
