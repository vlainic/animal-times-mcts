class_name ChaoticBotPlayer
extends BotPlayer

func _init():
	bot_type = "Chaotic"

# Chaotic bot phase handlers with chaotic-specific logic
func handle_reinforce_phase():
	# print("Bot ", player_name, ": *** REINFORCE PHASE HANDLER CALLED ***")
	var delay = 0.01 if Globals.FAST_FORWARD else 1.0
	await get_tree().create_timer(delay).timeout
	
	# Recalculate territories with current unit counts (fresh data every turn)
	# print("Bot ", player_name, ": Recalculating territories for new turn...")
	_calculate_owned_territory_indexes()
	
	# print("Bot ", player_name, ": Skipping REINFORCE phase")
	# print("Bot ", player_name, ": REINFORCE phase complete")
	# Server will handle phase advancement after await completes

func handle_attack_phase():
	# print("Bot ", player_name, ": *** ATTACK PHASE HANDLER CALLED ***")
	var delay = 0.01 if Globals.FAST_FORWARD else 1.0
	await get_tree().create_timer(delay).timeout
	# print("=== ", bot_type, " BOT ", player_name, " ATTACK PHASE ===")
	# print(peer_id)
	
	var attack = {}
	if Globals.IS_TUTORIAL_MODE and peer_id < 1002:
		attack = _pick_tutorial_attack()
	else:
		attack = _pick_random_attack()
	
	if not attack.is_empty():
		# print(bot_type, " Bot ", player_name, ": Random attack on ", attack.defending_territory, " from ", attack.attacking_territory)
		
		# Convert attack object to proper params format for Server.request_combat()
		var params = {
			"attacker": attack.attacking_territory.replace(" ", "_"),  # Convert to node name format
			"defender": attack.defending_territory.replace(" ", "_"),  # Convert to node name format
			"one_round_only": true,  # All bots do one-round combat for now
			"requester_peer_id": peer_id  # BOT FIX: Pass bot's peer ID explicitly
		}
		Server.request_combat(params)
		
		# Wait for combat to complete, then advance phase
		var combat_delay = 0.01 if Globals.FAST_FORWARD else 2.0
		await get_tree().create_timer(combat_delay).timeout
		# print(bot_type, " Bot ", player_name, ": Combat completed, advancing phase")
		# print("Bot ", player_name, ": ATTACK phase complete")
		# Server will handle phase advancement after await completes
	else:
		# print(bot_type, " Bot ", player_name, ": No valid attacks available")
		Server.mark_attack_performed()
		# print("Bot ", player_name, ": ATTACK phase complete")
		# Server will handle phase advancement after await completes

func handle_deploy_phase():
	var delay = 0.01 if Globals.FAST_FORWARD else 1.0
	await get_tree().create_timer(delay).timeout
	# print("Bot ", player_name, ": *** DEPLOY PHASE HANDLER CALLED ***")
	
	# Get pending armies directly from server (more reliable than metadata)
	var bonus_armies = Server.get_bot_pending_armies(peer_id)
	# print("Bot ", player_name, ": Checking for pending armies: ", bonus_armies)
	
	if bonus_armies > 0:
		# print("Bot ", player_name, ": Deploying ", bonus_armies, " bonus armies")
		await _deploy_bonus_armies(bonus_armies)
		# Clear the bonus armies after deployment using server function
		Server.clear_bot_pending_armies(peer_id)
		# print("Bot ", player_name, ": Finished deploying all bonus armies")
	else:
		# print("Bot ", player_name, ": No bonus armies to deploy")
		pass
	
	# print("Bot ", player_name, ": Deploy phase complete, advancing to FORTIFY")
	# Server will handle phase advancement after await completes

func handle_fortify_phase():
	# print("Bot ", player_name, ": *** FORTIFY PHASE HANDLER CALLED ***")
	var delay = 0.01 if Globals.FAST_FORWARD else 1.0
	await get_tree().create_timer(delay).timeout
	
	# # Debug: Print card count
	# var cards_node = get_node_or_null("Cards")
	# if cards_node:
	# 	print("Bot ", player_name, ": Has ", cards_node.get_child_count(), " cards")
	# else:
	# 	print("Bot ", player_name, ": No Cards node found")
	
	# print("Bot ", player_name, ": Skipping FORTIFY phase")
	# print("Bot ", player_name, ": FORTIFY phase complete")
	# Server will handle phase advancement after await completes

# Chaotic bot decision making - random attack selection
func _pick_random_attack() -> Dictionary:
	# print("Bot ", player_name, ": Picking random attack from ", owned_territory_indexes.size(), " owned territories")
	# print(owned_territory_indexes)
	
	if owned_territory_indexes.is_empty():
		# print("Bot ", player_name, ": No owned territories, cannot attack")
		return {}
	# print("Bot ", player_name, ": Owned territories: ", owned_territory_indexes)
	
	# Create a copy of owned territories to track which ones we've tried
	var available_attackers = owned_territory_indexes.duplicate()
	# print("Bot ", player_name, ": Available attackers: ", available_attackers)

	# Keep trying until we find a valid attack or run out of territories
	while not available_attackers.is_empty():
		# Pick a random attacker from remaining territories
		var attacker_index = available_attackers[randi() % available_attackers.size()]
		var attacker_name = _get_territory_name_by_index(attacker_index)
		
		# print("Bot ", player_name, ": Selected attacker: ", attacker_name, " (index: ", attacker_index, ")")
		
		# Get adjacent territories using the matrix
		var adjacent_indices = _get_neighbors_by_index(attacker_index)
		# print("Bot ", player_name, ": Adjacent territories: ", adjacent_indices)
		
		# Find enemy adjacent territories (not owned by this bot)
		var enemy_adjacent = []
		for neighbor_index in adjacent_indices:
			if not owned_territory_indexes.has(neighbor_index):
				if Globals.IS_TUTORIAL_MODE: # bots cannot attack human player in tutorial mode
					var territory_name = _get_territory_name_by_index(neighbor_index)
					if Server.map.find_territory(territory_name.replace(" ", "_")).get_owner_id() == 1:
						continue
				enemy_adjacent.append(neighbor_index)
		
		# print("Bot ", player_name, ": Enemy adjacent territories: ", enemy_adjacent)
		
		if _is_legal_attacker(attacker_index) and not enemy_adjacent.is_empty():
			# Found valid attack - pick random enemy
			var defender_index = enemy_adjacent[randi() % enemy_adjacent.size()]
			var defender_name = _get_territory_name_by_index(defender_index)
			
			# print("Bot ", player_name, ": Selected defender: ", defender_name, " (index: ", defender_index, ")")
			# print("Bot ", player_name, ": Attack selected: ", attacker_name, " -> ", defender_name)
			
			return {
				"attacking_territory": attacker_name,
				"defending_territory": defender_name
			}
		else:
			# No enemies for this attacker - remove it and try another
			available_attackers.erase(attacker_index)
			# print("Bot ", player_name, ": No enemy territories adjacent to ", attacker_name, " - trying another territory")
			# print("Bot ", player_name, ": Available attackers: ", available_attackers)
			# print(owned_territory_indexes)
	
	# No valid attacks found from any territory
	# print("Bot ", player_name, ": No valid attacks available from any territory")
	return {}

func _deploy_bonus_armies(army_count: int):
	"""Deploy bonus armies to random owned territories"""
	# print("Bot ", player_name, ": Starting deployment of ", army_count, " bonus armies to random territories")
	
	# Recalculate owned territories (they might have changed due to conquests)
	_calculate_owned_territory_indexes()
	# print("Bot ", player_name, ": Owned territories count: ", owned_territory_indexes.size())
	
	if owned_territory_indexes.is_empty():
		# print("Bot ", player_name, ": No owned territories to deploy armies to")
		return
	
	# Deploy armies to random territories using existing owned_territory_indexes
	for i in range(army_count):
		var random_index = owned_territory_indexes[randi() % owned_territory_indexes.size()]
		var territory_name = _get_territory_name_by_index(random_index)
		if not territory_name.is_empty():
			# Convert to underscore format for server
			var territory_name_underscore = territory_name.replace(" ", "_")
			# print("Bot ", player_name, ": Deploying bonus army ", i + 1, " to ", territory_name_underscore)
			
			# Use server's direct deployment function
			if Server:
				Server._deploy_army_to_territory_direct(territory_name_underscore, peer_id)
			
			# Small delay between deployments
			var delay = 0.01 if Globals.FAST_FORWARD else 0.2
			await get_tree().create_timer(delay).timeout
	
	# print("Bot ", player_name, ": Finished deploying all ", army_count, " bonus armies")

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

func _advance_phase_deferred():
	"""Advance phase after current phase processing is complete"""
	Server.advance_phase(peer_id)


func _pick_tutorial_attack() -> Dictionary:
	if peer_id == 1000:
		return {
			"attacking_territory": "Beaver Dam",
			"defending_territory": "The Delta"
		}
	elif peer_id == 1001:
		return {
			"attacking_territory": "Wind Plains",
			"defending_territory": "Bush Island"
		}
	else:
		return {}
