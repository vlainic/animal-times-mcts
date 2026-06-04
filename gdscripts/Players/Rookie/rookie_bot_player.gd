class_name RookieBotPlayer
extends BotPlayer

# Signals
signal deployment_completed

# Constants
const MISSION_FACTOR = 2
const DEPLOY_MULTIPLIER = 5

# Rookie bot specific variables
var stored_attack: Dictionary = {}  # Attack calculated during REINFORCE phase
var weighted_attack_options: Array = []  # All calculated attack options with weights
var territory_continent_map: Dictionary = {}  # Cached territory-continent mapping

func _init():
	bot_type = "Rookie"

func _ready():
	# Load territory-continent mapping once
	territory_continent_map = _load_territory_continent_mapping()

# Rookie bot phase handlers with rookie-specific logic
func handle_reinforce_phase():
	# print("=== ROOKIE [", player_name, "] REINFORCE START ===")
	var delay = 0.01 if Globals.FAST_FORWARD else 1.0
	await get_tree().create_timer(delay).timeout
	
	# Recalculate territories with current unit counts (fresh data every turn)
	# print(">>> ROOKIE [", player_name, "] Recalculating territories...")
	_calculate_owned_territory_indexes()
	# print("    Owned territories count: ", owned_territory_indexes.size())
	
	# Rookie REINFORCE logic: "Initial calculation" + smart consolidation
	# Step 1: Calculate weighted attack options (Initial calculation)
	# print(">>> ROOKIE [", player_name, "] Calculating weighted attacks...")
	weighted_attack_options = _calculate_weighted_attacks()
	# print("    Attack options calculated: ", weighted_attack_options.size())
	
	# Step 2: Store the best attack for ATTACK phase
	# print(">>> ROOKIE [", player_name, "] Selecting best attack...")
	stored_attack = _select_best_attack()
	# if stored_attack.is_empty():
	# 	print("!!! ROOKIE [", player_name, "] No attack stored (empty)")
	# else:
	# 	print("    Stored attack: ", stored_attack.attacking_territory, " -> ", stored_attack.defending_territory)
	
	# Step 3: Smart consolidation based on attack opportunities
	# print(">>> ROOKIE [", player_name, "] Smart consolidating units...")
	await _smart_consolidate_units(stored_attack)
	
	# print("=== ROOKIE [", player_name, "] REINFORCE COMPLETE ===")
	# Server will handle phase advancement after await completes

func handle_attack_phase():
	# print("=== ROOKIE [", player_name, "] ATTACK START ===")
	var delay = 0.01 if Globals.FAST_FORWARD else 1.0
	await get_tree().create_timer(delay).timeout
	
	var loop_count = 0
	# Loop to handle multiple attacks (including overruns)
	while true:
		loop_count += 1
		# print(">>> ROOKIE [", player_name, "] Attack Loop Iter: ", loop_count, "/3")
		
		if loop_count > 3:  # Limit to 3 attack attempts
			# print("!!! ROOKIE [", player_name, "] Attack limit reached, ending")
			break
		
		# Use pre-calculated attack from REINFORCE phase
		var attack = stored_attack
		if attack.is_empty():
			# print("!!! ROOKIE [", player_name, "] No stored attack, recalculating...")
			weighted_attack_options = _calculate_weighted_attacks()
			attack = _select_best_attack()
			# print("    Recalculated attack: ", "empty" if attack.is_empty() else (attack.attacking_territory + " -> " + attack.defending_territory))
		else:
			pass # print("    Using stored attack: ", attack.attacking_territory, " -> ", attack.defending_territory)
		
		if attack.is_empty():
			# print("!!! ROOKIE [", player_name, "] No attacks available, marking and breaking")
			Server.mark_attack_performed()
			break
		
		# print(">>> ROOKIE [", player_name, "] Executing combat...")
		var attacker_units_before = _get_territory_unit_count(attack.attacking_territory)
		# print("    Attacker units before: ", attacker_units_before)

		# Convert attack object to proper params format for Server.request_combat()
		var params = {
			"attacker": attack.attacking_territory.replace(" ", "_"),  # Convert to node name format
			"defender": attack.defending_territory.replace(" ", "_"),  # Convert to node name format
			"one_round_only": true,  # All bots do one-round combat for now
			"requester_peer_id": peer_id  # BOT FIX: Pass bot's peer ID explicitly
		}
		# print("    Requesting combat on server...")
		Server.request_combat(params)
		
		# Mark that an attack was performed this turn
		# print("    Marking attack performed...")
		Server.mark_attack_performed()

		# Wait for combat to complete
		# print("    Waiting for combat to complete...")
		var combat_delay = 0.01 if Globals.FAST_FORWARD else 2.0
		await get_tree().create_timer(combat_delay).timeout
		# print("    Combat wait complete")

		var attacker_units_after = _get_territory_unit_count(attack.attacking_territory)
		# print("    Attacker units after: ", attacker_units_after)
		
		# Check for overrun
		var overrun_detected = is_overrun(attack.attacking_territory, attack.defending_territory, peer_id, attacker_units_before, attacker_units_after)
		# print("    Overrun detected: ", overrun_detected)
		
		if overrun_detected:
			# print(">>> ROOKIE [", player_name, "] OVERRUN - handling...")
			var overrun_delay = 0.01 if Globals.FAST_FORWARD else 2.0
			await get_tree().create_timer(overrun_delay).timeout
			var continue_attacking = await handle_overrun(attack.attacking_territory, attack.defending_territory, peer_id)
			# print("    handle_overrun returned: ", continue_attacking)
			
			if continue_attacking:
				# print(">>> ROOKIE [", player_name, "] Continuing attack loop")
				continue
			else:
				# print(">>> ROOKIE [", player_name, "] No more attacks after overrun, breaking")
				break
		else:
			# print(">>> ROOKIE [", player_name, "] Normal attack (no overrun), breaking")
			break

	# print("=== ROOKIE [", player_name, "] ATTACK COMPLETE ===")
	# Server will handle phase advancement after await completes

func handle_deploy_phase():
	# print("=== ROOKIE [", player_name, "] DEPLOY START ===")
	var delay = 0.01 if Globals.FAST_FORWARD else 1.0
	await get_tree().create_timer(delay).timeout
	
	# Get pending armies directly from server (more reliable than metadata)
	var bonus_armies = Server.get_bot_pending_armies(peer_id)
	# print("    Bonus armies to deploy: ", bonus_armies)
	
	if bonus_armies > 0:
		# print(">>> ROOKIE [", player_name, "] Deploying ", bonus_armies, " armies...")
		await _deploy_bonus_armies(bonus_armies)
		# Clear the bonus armies after deployment using server function
		Server.clear_bot_pending_armies(peer_id)
		# print("    Deployment complete, emitting signal")
		# Emit signal that deployment is complete
		deployment_completed.emit()
	else:
		# print("    No armies to deploy, emitting completion signal")
		# Even if no armies, emit completion signal
		deployment_completed.emit()
	
	# print("=== ROOKIE [", player_name, "] DEPLOY COMPLETE ===")
	# Server will handle phase advancement after await completes

func handle_fortify_phase():
	# print("=== ROOKIE [", player_name, "] FORTIFY START ===")
	var delay = 0.01 if Globals.FAST_FORWARD else 1.0
	await get_tree().create_timer(delay).timeout
	
	# Rookie FORTIFY logic: redistribute units
	# print(">>> ROOKIE [", player_name, "] Redistributing units...")
	await _redistribute_units()
	
	# print("=== ROOKIE [", player_name, "] FORTIFY COMPLETE ===")
	# Server will handle phase advancement after await completes

# Rookie bot decision making - calculate weighted attack options (REINFORCE phase)
func _calculate_weighted_attacks(overrun_mode: bool = false) -> Array:
	"""Calculate all legal attacker-defender pairs with weights (Initial calculation)"""
	# print("Bot ", player_name, ": Calculating weighted attacks from ", owned_territory_indexes.size(), " owned territories")
	
	if owned_territory_indexes.is_empty():
		# print("Bot ", player_name, ": No owned territories, cannot attack")
		return []
	
	# Find all legal attacker-defender pairs with weights
	var attack_options = []
	var total_weight = 0.0
	
	for attacker_index in owned_territory_indexes:
		if not _is_legal_attacker(attacker_index):
			continue
			
		var attacker_name = _get_territory_name_by_index(attacker_index)
		var attacker_units = _get_territory_unit_count(attacker_name)
		
		# Get adjacent enemy territories
		var adjacent_indices = _get_neighbors_by_index(attacker_index)
		for defender_index in adjacent_indices:
			if owned_territory_indexes.has(defender_index):
				continue  # Skip allied territories
				
			var defender_name = _get_territory_name_by_index(defender_index)
			var defender_units = _get_territory_unit_count(defender_name)
			
			# Calculate base weight: attacking_units / defending_units
			var base_weight = float(attacker_units) / float(defender_units) if defender_units > 0 else float(attacker_units)
			
			# Calculate mission factor
			var mission_factor = _calculate_mission_factor(defender_name)
			
			# Apply mission factor to weight
			var weight = base_weight * mission_factor
			
			attack_options.append({
				"attacking_territory": attacker_name,
				"defending_territory": defender_name,
				"weight": weight,
				"base_weight": base_weight,
				"mission_factor": mission_factor,
				"attacker_units": attacker_units,
				"defender_units": defender_units
			})
			total_weight += weight

			# print("Bot ", player_name, ": Calculated weighted attack: ", attacker_name, " -> ", defender_name, " (attacker units: ", attacker_units, ")")
	
	# Sort by weight (highest first) for smart consolidation decisions
	attack_options.sort_custom(func(a, b): return a.weight > b.weight)
	
	# print(len(attack_options))
	# var ao_weights: Array[float] = []
	# for option in attack_options:
	# 	ao_weights.append(option["weight"])
	# print("Bot ", player_name, ": AO Weights: ", ao_weights)

	# Tiered selection: first try all above MISSION_FACTOR, then all above 1, then top 5
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
	
	# Recalculate total weight for top 3 options only
	total_weight = 0.0
	for option in attack_options:
		total_weight += option["weight"]
	
	# Normalize weights to probabilities for top 3 options
	for option in attack_calculate_mission_factor_calculate_mission_factor_options:
		option["probability"] = option["weight"] / total_weight if total_weight > 0 else 0.0
	
	# print("Bot ", player_name, ": Calculated ", attack_options.size(), " weighted attack options (top 3)")
	return attack_options

# Select the best attack from calculated options
func _select_best_attack() -> Dictionary:
	"""Select the best attack from pre-calculated weighted options"""
	if weighted_attack_options.is_empty():
		# print("!!! ROOKIE [", player_name, "] _select_best_attack: No valid attacks available")
		return {}
	
	# Pick randomly from weighted distribution
	var random_value = randf()
	var cumulative_probability = 0.0
	
	for option in weighted_attack_options:
		cumulative_probability += option["probability"]
		if random_value <= cumulative_probability:
			return {
				"attacking_territory": option.attacking_territory,
				"defending_territory": option.defending_territory
			}
	
	# Fallback to first option (highest weight)
	# print("    _select_best_attack: Using fallback (first option)")
	return {
		"attacking_territory": weighted_attack_options[0].attacking_territory,
		"defending_territory": weighted_attack_options[0].defending_territory
	}

# Helper function to get territory unit count
func _get_territory_unit_count(territory_name: String) -> int:
	"""Get unit count for a territory by name"""
	if not Server or not Server.map:
		return 0
		
	var all_territories = Server.map.get_all_territories()
	for territory in all_territories:
		if territory.name == territory_name.replace(" ", "_"):
			return territory.get_unit_count()
	return 0

# Calculate mission factor for a defender territory
func _calculate_mission_factor(defender_territory_name: String) -> int:
	"""Calculate MISSION_FACTOR for a defender territory"""
	var mission = _get_current_mission()
	if mission.is_empty():
		return 1
	
	var mission_type = mission.get("type", "")
	var mission_id = mission.get("id", "")
	var defender_continent = _get_territory_continent(defender_territory_name)
	
	# Elimination: smart prioritization based on strategic situation
	if mission_type == "elimination":
		return _calculate_elimination_mission_factor(defender_territory_name)
	
	# Conquest missions
	if mission_type == "conquest":
		var any_third = mission.get("any_third", false)
		if any_third:
			# 1 Continent of Choice (any_third=true): pick best continent; includes mission-continent fast-path
			return _get_continent_of_choice_mission_factor(defender_territory_name)
		# Otherwise: fixed continents from mission; weight only if defender is in one
		return MISSION_FACTOR if _is_territory_in_mission_continent(defender_territory_name) else 1
	
	# Special: territory_count/continent_count variants are encoded via mission.id
	# sTriple => pursue three continents where we miss the least
	if mission_id == "sTriple":
		return _get_three_continents_mission_factor(defender_continent)
	
	# 20 territories or other specials: no positional preference
	return 1

# Get bot's current mission data
func _get_current_mission() -> Dictionary:
	"""Get bot's current mission data"""
	# Use the Player class get_mission() function
	var mission = get_mission()
	if mission.is_empty():
		return {
			"id": "",
			"type": "none",
			"continents": [],
			"target_animal": "",
			"territory_count": 0,
			"any_third": false
		}
	
	# Parse mission data based on mission type
	var mission_data = {
		"id": mission.get("id", ""),
		"type": "none",
		"continents": [],
		"target_animal": "",
		"territory_count": 0,
		"any_third": false,
		"is_first_target": true
	}
	
	# Determine mission type and extract relevant data
	if mission.has("continents"):
		mission_data.type = "conquest"
		mission_data.continents = mission.continents
		mission_data.any_third = mission.get("any_third", false)
	elif mission.has("target_animal"):
		mission_data.type = "elimination"
		mission_data.target_animal = mission.target_animal
		mission_data.territory_count = mission.get("fallback_territories", 0)
		mission_data.is_first_target = mission.get("is_first_target", true)
	elif mission.has("territory_count"):
		mission_data.type = "special"
		mission_data.territory_count = mission.territory_count
	elif mission.has("continent_count"):
		mission_data.type = "special"
		mission_data.continent_count = mission.continent_count
	
	return mission_data

# Get continent for a territory
func _get_territory_continent(territory_name: String) -> String:
	"""Get continent for a territory"""
	# Convert territory name to underscore format for lookup
	var territory_key = territory_name.replace(" ", "_")
	return territory_continent_map.get(territory_key, "Unknown")

# Load territory-continent mapping from territory_names.json
func _load_territory_continent_mapping() -> Dictionary:
	"""Load territory-continent mapping from territory_names.json"""
	var mapping = {}
	
	# Load territory_names.json
	var file = FileAccess.open("res://Map/Territories/territory_names.json", FileAccess.READ)
	if file:
		var json_string = file.get_as_text()
		file.close()
		var json = JSON.new()
		var parse_result = json.parse(json_string)
		if parse_result == OK:
			var territory_data = json.data
			
			# Build territory-continent mapping
			for continent in territory_data:
				var territories = territory_data[continent]
				for territory in territories:
					# Convert territory name to underscore format
					var territory_key = territory.replace(" ", "_")
					mapping[territory_key] = continent
	
	return mapping

# Check if territory is in mission continent
func _is_territory_in_mission_continent(territory_name: String) -> bool:
	"""Check if territory is part of the bot's mission continent"""
	var current_mission = _get_current_mission()
	if current_mission.type != "conquest":
		return false
	
	var territory_continent = _get_territory_continent(territory_name)
	return current_mission.continents.has(territory_continent)

# Check if territory is owned by mission player target
func _is_territory_owned_by_mission_target(territory_name: String) -> bool:
	"""Check if territory is owned by the bot's mission player target"""
	var current_mission = _get_current_mission()
	if current_mission.type != "elimination" or current_mission.target_animal.is_empty():
		return false
	
	# Find player with target_animal and check if they own the territory
	var target_player_id = _find_player_by_animal(current_mission.target_animal)
	if target_player_id == -1:
		return false
	
	# Check if territory is owned by target player
	return _is_territory_owned_by_player(territory_name, target_player_id)

# Find player ID by animal name
func _find_player_by_animal(animal_name: String) -> int:
	"""Find player ID by animal name"""
	if not Server or not Server.game_players:
		return -1
	
	for player in Server.game_players:
		if player.has_method("get_animal") and player.get_animal() == animal_name:
			return player.peer_id
	
	return -1

# Check if territory is owned by specific player
func _is_territory_owned_by_player(territory_name: String, player_id: int) -> bool:
	"""Check if territory is owned by specific player"""
	if not Server or not Server.map:
		return false
	
	var all_territories = Server.map.get_all_territories()
	for territory in all_territories:
		if territory.name == territory_name.replace(" ", "_"):
			# Check if territory is owned by the player
			return territory.get_owner_id() == player_id
	
	return false

# Get current player's territory count
func _get_player_territory_count() -> int:
	"""Get current player's territory count"""
	return owned_territory_indexes.size()

# Get target player's territory count
func _get_target_player_territory_count() -> int:
	"""Get target player's territory count for elimination missions"""
	var current_mission = _get_current_mission()
	if current_mission.type != "elimination" or current_mission.target_animal.is_empty():
		return 0
	
	var target_player_id = _find_player_by_animal(current_mission.target_animal)
	if target_player_id == -1:
		return 0
	
	# Count territories owned by target player
	var target_territory_count = 0
	if not Server or not Server.map:
		return 0
		
	var all_territories = Server.map.get_all_territories()
	for territory in all_territories:
		if territory.get_owner_id() == target_player_id:
			target_territory_count += 1
	
	return target_territory_count

# Calculate smart elimination mission factor
func _calculate_elimination_mission_factor(defender_territory_name: String) -> int:
	"""Calculate smart mission factor for elimination missions"""
	var current_mission = _get_current_mission()
	if current_mission.type != "elimination":
		return 1
	
	# For first target missions, only prioritize target player territories
	var factor = 1
	var is_first_target = current_mission.get("is_first_target", true)		
	if is_first_target:
		# Check if territory is owned by target player (highest priority)
		factor = MISSION_FACTOR if _is_territory_owned_by_mission_target(defender_territory_name) else 1
	else:	
		# Get strategic information
		var current_territories = _get_player_territory_count()
		var target_territories = _get_target_player_territory_count()
		var fallback_territories = current_mission.get("territory_count", 20)
		
		
		# For fallback missions, calculate strategic value
		var territories_to_20 = fallback_territories - current_territories
		var target_remaining = target_territories
		
		# IF target_remaining > territories_to_20 -> return 1
		# ELSE return MISSION_FACTOR if _is_territory_owned_by_mission_target() else 1
		if target_remaining > territories_to_20:
			factor = 1
		else:
			factor = MISSION_FACTOR if _is_territory_owned_by_mission_target(defender_territory_name) else 1
		
	# print("Bot ", player_name, ": Elimination - Fallback mode: current=", current_territories, ", target=", target_territories, ", to_20=", territories_to_20, ", closer_goal=", closer_goal, ", factor=", factor)
	return factor

# Get all continents
func _get_all_continents() -> Array:
	"""Get list of all continents"""
	return ["Mudflats", "Eucalypta", "Peaks", "Riverside", "Bushlands", "Bamboovia"]

# Get territories in a continent
func _get_continent_territories(continent_name: String) -> Array:
	"""Get territories in a continent"""
	# Load territory data from territory_names.json
	var file = FileAccess.open("res://Map/Territories/territory_names.json", FileAccess.READ)
	if file:
		var json_string = file.get_as_text()
		file.close()
		var json = JSON.new()
		var parse_result = json.parse(json_string)
		if parse_result == OK:
			var territory_data = json.data
			if territory_data.has(continent_name):
				# Convert territory names to underscore format
				var territories = territory_data[continent_name]
				var underscore_territories = []
				for territory in territories:
					underscore_territories.append(territory.replace(" ", "_"))
				return underscore_territories
	
	return []

# Count owned territories per continent
func _count_owned_territories_per_continent() -> Dictionary:
	"""Count owned territories per continent"""
	var continent_counts = {}
	var all_continents = _get_all_continents()
	
	# Initialize counts
	for continent in all_continents:
		continent_counts[continent] = 0
	
	# Count owned territories per continent
	for territory_index in owned_territory_indexes:
		var territory_name = _get_territory_name_by_index(territory_index)
		var continent = _get_territory_continent(territory_name)
		if continent != "Unknown":
			continent_counts[continent] += 1
	
	return continent_counts

func _deploy_bonus_armies(army_count: int):
	"""Deploy bonus armies to mission-relevant territories using weighted selection"""
	# Recalculate owned territories (they might have changed due to conquests)
	_calculate_owned_territory_indexes()
	
	if owned_territory_indexes.is_empty():
		print("Bot ", player_name, ": ERROR - No owned territories!")
		return
	
	# Calculate weighted deployment options based on mission factors
	var deployment_options = _calculate_deployment_weights()
	
	if deployment_options.is_empty():
		print("Bot ", player_name, ": ERROR - No deployment options available!")
		return
	
	# Deploy armies using weighted selection
	for i in range(army_count):
		var selected_territory = _select_deployment_territory(deployment_options)
		if not selected_territory.is_empty():
			# Convert to underscore format for server
			var territory_name_underscore = selected_territory.replace(" ", "_")
			
			# Use server's direct deployment function
			if Server:
				Server._deploy_army_to_territory_direct(territory_name_underscore, peer_id)
			else:
				print("Bot ", player_name, ": ERROR - Server reference is null!")
			
			# Small delay between deployments
			var delay = 0.01 if Globals.FAST_FORWARD else 0.2
			await get_tree().create_timer(delay).timeout

# Calculate weighted deployment options based on mission factors
func _calculate_deployment_weights() -> Array:
	"""Calculate deployment weights for all owned territories based on mission factors"""
	var deployment_options = []
	var total_weight = 0.0
	
	for territory_index in owned_territory_indexes:
		var territory_name = _get_territory_name_by_index(territory_index)
		if territory_name.is_empty():
			continue
		
		# Calculate enhanced mission factor for deployment (territory + attack potential)
		var mission_factor = _calculate_deployment_mission_factor(territory_name)
		if mission_factor > 1:
			mission_factor *= DEPLOY_MULTIPLIER
		
		deployment_options.append({
			"territory_name": territory_name,
			"weight": float(mission_factor)
		})
		total_weight += float(mission_factor)
	
	# Normalize weights to probabilities
	for option in deployment_options:
		option["probability"] = option["weight"] / total_weight if total_weight > 0 else 0.0
	
	return deployment_options

# Select deployment territory using weighted random selection
func _select_deployment_territory(deployment_options: Array) -> String:
	"""Select a territory for deployment using weighted random selection"""
	if deployment_options.is_empty():
		return ""
	
	# Pick randomly from weighted distribution
	var random_value = randf()
	var cumulative_probability = 0.0
	
	for option in deployment_options:
		cumulative_probability += option["probability"]
		if random_value <= cumulative_probability:
			return option["territory_name"]
	
	# Fallback to first option
	return deployment_options[0]["territory_name"]

# Calculate enhanced mission factor for deployment (territory + attack potential)
func _calculate_deployment_mission_factor(territory_name: String) -> int:
	"""Calculate deployment mission factor including attack potential"""
	# Base mission factor for the territory itself
	var base_factor = _calculate_mission_factor(territory_name)
	
	# Calculate attack potential - sum mission factors of all attackable neighbors
	var attack_potential = 0
	var territory_index = _get_territory_index_by_name(territory_name)
	if territory_index == -1:
		return base_factor
		
	var neighbors = _get_neighbors_by_index(territory_index)
	
	for neighbor_index in neighbors:
		if not owned_territory_indexes.has(neighbor_index):  # Enemy territory
			var neighbor_name = _get_territory_name_by_index(neighbor_index)
			attack_potential += _calculate_mission_factor(neighbor_name)
	
	# Total deployment factor = base + attack potential (no cap, same weight)
	return base_factor + attack_potential

func _advance_phase_deferred():
	"""Advance phase after current phase processing is complete"""
	# print(">>> BOT ", player_name, " [", peer_id, "]: _advance_phase_deferred() CALLED")
	# print("    Current server phase: ", Server.GamePhase.keys()[Server.get_current_phase()])
	# print("    Current player: ", Server.get_current_player_peer_id())
	# print("    My peer_id: ", peer_id)
	
	# if Server.get_current_player_peer_id() != peer_id:
	# 	print("    WARNING: Not my turn, but advancing anyway!")
	
	Server.advance_phase(peer_id)
	# print(">>> BOT ", player_name, ": _advance_phase_deferred() COMPLETE")

# REINFORCE phase: smart consolidation based on stored attack
func _smart_consolidate_units(target_attack: Dictionary):
	"""Smart consolidation for specific stored attack"""
	# print("Bot ", player_name, ": Starting smart unit consolidation for stored attack...")
	
	if target_attack.is_empty():
		# print("Bot ", player_name, ": No stored attack, skipping consolidation")
		return
	
	var attacker_territory = target_attack.attacking_territory
	var attacker_units = _get_territory_unit_count(attacker_territory)
	
	# If attacker already has 4+ units, no consolidation needed
	if attacker_units >= 4:
		# print("Bot ", player_name, ": Attacker ", attacker_territory, " already has ", attacker_units, " units, no consolidation needed")
		return
	
	# Find all owned territories adjacent to the attacker territory
	var attacker_index = _get_territory_index_by_name(attacker_territory)
	if attacker_index == -1:
		# print("Bot ", player_name, ": ERROR - Attacker territory index not found: ", attacker_territory)
		return
		
	var adjacent_indices = _get_neighbors_by_index(attacker_index)
	var allied_adjacent = []
	
	for adj_index in adjacent_indices:
		if owned_territory_indexes.has(adj_index):
			var adj_name = _get_territory_name_by_index(adj_index)
			var adj_units = _get_territory_unit_count(adj_name)
			allied_adjacent.append({"name": adj_name, "units": adj_units})
	
	# print("Bot ", player_name, ": Found ", allied_adjacent.size(), " allied adjacent territories to ", attacker_territory)
	
	# Go through each allied adjacent territory and consolidate units
	for allied_territory in allied_adjacent:
		if attacker_units >= 4:
			break  # We have enough units now
			
		var units_to_move = allied_territory.units - 1  # Leave 1 unit behind
		if units_to_move > 0:
			# print("Bot ", player_name, ": Moving ", units_to_move, " units from ", allied_territory.name, " to ", attacker_territory)
			_move_units_between_territories(allied_territory.name, attacker_territory, units_to_move)
			attacker_units += units_to_move
	
	# print("Bot ", player_name, ": Consolidation complete. ", attacker_territory, " now has ", attacker_units, " units")

# FORTIFY phase: redistribute units for better defense
func _redistribute_units():
	"""Go through all territories and move units from higher to lower if difference > 1"""
	# print("Bot ", player_name, ": Starting unit redistribution...")
	
	# Recalculate owned territories (they might have changed due to conquests/movements)
	_calculate_owned_territory_indexes()
	
	# Get all owned territories with their unit counts
	var territory_data = []
	for territory_index in owned_territory_indexes:
		var territory_name = _get_territory_name_by_index(territory_index)
		var unit_count = _get_territory_unit_count(territory_name)
		territory_data.append({
			"index": territory_index,
			"name": territory_name,
			"units": unit_count
		})
	
	# Find legal moving pairs (adjacent territories)
	var _moves_made = 0
	for i in range(territory_data.size()):
		for j in range(i + 1, territory_data.size()):
			var territory1 = territory_data[i]
			var territory2 = territory_data[j]
			
			# Check if territories are adjacent
			var adjacent_indices = _get_neighbors_by_index(territory1.index)
			if not adjacent_indices.has(territory2.index):
				continue
			
			# Check if difference is > 1
			var difference = abs(territory1.units - territory2.units)
			if difference <= 1:
				continue
			
			# Move single unit from higher to lower
			var source_territory = territory1 if territory1.units > territory2.units else territory2
			var dest_territory = territory2 if territory1.units > territory2.units else territory1
			
			# Move 1 unit
			_move_units_between_territories(source_territory.name, dest_territory.name, 1)
			_moves_made += 1
			
			# Update the data for next iterations
			source_territory.units -= 1
			dest_territory.units += 1
			
			# Small delay between moves
			var delay = 0.01 if Globals.FAST_FORWARD else 0.1
			await get_tree().create_timer(delay).timeout
	
	# print("Bot ", player_name, ": Unit redistribution complete (", _moves_made, " moves made)")

# Helper function to move units between territories (uses server so sync + deploy effect run for all)
func _move_units_between_territories(source_name: String, dest_name: String, unit_count: int):
	"""Move units between two territories via server; triggers sync and deploy effect on destination."""
	if not Server or not Server.has_method("request_territory_action_for_peer"):
		return
	var source_underscore = source_name.replace(" ", "_")
	var dest_underscore = dest_name.replace(" ", "_")
	for i in range(unit_count):
		Server.request_territory_action_for_peer(dest_underscore, "move_unit_to", {"source_territory": source_underscore}, peer_id)
		var delay = 0.01 if Globals.FAST_FORWARD else 0.05
		await get_tree().create_timer(delay).timeout

# === CONTINENT ANALYSIS FUNCTIONS ===
# (Functions already exist above, no need to duplicate)

# === SPECIAL MISSION FACTOR FUNCTIONS ===

# Get mission factor for 3 Continents Mission
func _get_three_continents_mission_factor(defender_continent: String) -> int:
	"""Calculate mission factor for 3 Continents Mission"""
	var owned_per_continent = _count_owned_territories_per_continent()
	var total_per_continent = {}
	
	# Calculate total territories per continent
	for continent in _get_all_continents():
		var continent_territories = _get_continent_territories(continent)
		total_per_continent[continent] = continent_territories.size()
	
	# Calculate missing territories per continent
	var missing_per_continent = {}
	for continent in _get_all_continents():
		var owned = owned_per_continent.get(continent, 0)
		var total = total_per_continent.get(continent, 0)
		missing_per_continent[continent] = total - owned
	
	# Find the three continents with least missing territories
	var sorted_continents = []
	for continent in _get_all_continents():
		sorted_continents.append({
			"name": continent,
			"missing": missing_per_continent.get(continent, 0)
		})
	
	# Sort by missing count (ascending)
	sorted_continents.sort_custom(func(a, b): return a.missing < b.missing)
	
	# Get the top 3 continents with least missing territories
	var top_three = []
	for i in range(min(3, sorted_continents.size())):
		top_three.append(sorted_continents[i].name)
	
	# Return MISSION_FACTOR if defender continent is in top 3, otherwise 1
	if defender_continent in top_three:
		# print("Bot ", player_name, ": 3 Continents Mission - ", defender_continent, " is in top 3, factor = ", MISSION_FACTOR)
		return MISSION_FACTOR
	# print("Bot ", player_name, ": 3 Continents Mission - ", defender_continent, " not in top 3, factor = 1")
	return 1

# Get mission factor for 1 Continent of Choice Mission

func _get_continent_of_choice_mission_factor(defender_territory_name: String) -> int:
	"""Calculate mission factor for 1 Continent of Choice Mission with mission-continent fast-path"""
	# If this territory is already in current mission continents, prefer it immediately
	if _is_territory_in_mission_continent(defender_territory_name):
		return MISSION_FACTOR
	
	var defender_continent = _get_territory_continent(defender_territory_name)
	var owned_per_continent = _count_owned_territories_per_continent()
	var total_per_continent = {}
	
	# Calculate total territories per continent
	for continent in _get_all_continents():
		var continent_territories = _get_continent_territories(continent)
		total_per_continent[continent] = continent_territories.size()
	
	# Calculate missing territories per continent
	var missing_per_continent = {}
	var eligible_continents = _get_all_continents()
	
	# Exclude current mission's fixed continents from eligibility
	var current_mission = _get_current_mission()
	for c in current_mission.get("continents", []):
		if eligible_continents.has(c):
			eligible_continents.erase(c)
	
	for continent in eligible_continents:
		var owned = owned_per_continent.get(continent, 0)
		var total = total_per_continent.get(continent, 0)
		missing_per_continent[continent] = total - owned
	
	# Find the continent with least missing territories (excluding Eucalypta and Peaks)
	var best_continent = ""
	var least_missing = 999
	
	for continent in eligible_continents:
		var missing = missing_per_continent.get(continent, 0)
		if missing < least_missing:
			least_missing = missing
			best_continent = continent
	
	# Return MISSION_FACTOR if defender continent is the best choice, otherwise 1
	if defender_continent == best_continent:
		# print("Bot ", player_name, ": 1 Continent of Choice Mission - ", defender_continent, " is best choice, factor = ", MISSION_FACTOR)
		return MISSION_FACTOR
	# print("Bot ", player_name, ": 1 Continent of Choice Mission - ", defender_continent, " not best choice, factor = 1")
	return 1

# Check if an overrun occurred during combat
func is_overrun(_attacking_territory: String, defending_territory: String, attacker_peer_id: int, attacker_units_before: int, attacker_units_after: int) -> bool:
	# Check if attacker units are the same before and after
	var units_unchanged = attacker_units_before == attacker_units_after
	
	# Check if defending territory is now owned by the attacker
	var territory_conquered = _is_territory_owned_by_player(defending_territory, attacker_peer_id)
	
	# print("    is_overrun check: units_unchanged=", units_unchanged, ", territory_conquered=", territory_conquered)
	return units_unchanged and territory_conquered

# Handle overrun situation by moving all units except 1 to conquered territory
func handle_overrun(attacking_territory: String, defending_territory: String, _attacker_peer_id: int) -> bool:
	"""Handle overrun unit movement and return whether more attacks are available"""
	# print(">>> ROOKIE [", player_name, "] handle_overrun() START")
	# print("    From: ", attacking_territory, " To: ", defending_territory)
	
	# Get current unit count in attacking territory
	var current_units = _get_territory_unit_count(attacking_territory)
	# print("    Current units in attacker territory: ", current_units)
	
	# Calculate units to move (all except 1)
	var units_to_move = current_units - 1
	# print("    Units to move: ", units_to_move)
	
	if units_to_move > 0:
		# print("    Moving units...")
		# Use the existing helper function to move units
		await _move_units_between_territories(attacking_territory, defending_territory, units_to_move)
		# print("    Movement complete")

	# print(">>> ROOKIE [", player_name, "] Recalculating attacks after overrun...")
	# Recalculate owned territories
	_calculate_owned_territory_indexes()
	# print("    Owned territories: ", owned_territory_indexes.size())
	
	# Calculate new weighted attack options (overrun mode = true)
	weighted_attack_options = _calculate_weighted_attacks(true)
	# print("    New attack options: ", weighted_attack_options.size())
	
	if weighted_attack_options.is_empty():
		# print("!!! ROOKIE [", player_name, "] No attacks after overrun, returning FALSE")
		return false  # No more attacks available
	else:
		# Store the best attack for next iteration
		stored_attack = _select_best_attack()
		if stored_attack.is_empty():
			# print("!!! ROOKIE [", player_name, "] Best attack is empty, returning FALSE")
			return false
		# print(">>> ROOKIE [", player_name, "] Next attack ready: ", stored_attack.attacking_territory, " -> ", stored_attack.defending_territory)
		return true  # More attacks available
