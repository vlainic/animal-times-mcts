# Players/achievement_manager.gd
class_name AchievementManager
extends Node

var session_eligible: bool = false
var had_one_territory: bool = false

func _ready() -> void:
	if Steam.has_signal("user_stats_received"):
		Steam.user_stats_received.connect(_on_user_stats_received)

func _input(event: InputEvent) -> void:
	if not OS.is_debug_build():
		return
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_F8:
		if not SteamManager.is_steam_initialized:
			return
		Steam.resetAllStats(true)
		Steam.storeStats()
		print("Achievements reset")

func _on_user_stats_received(_game_id: int, _result: int, _user_id: int) -> void:
	pass

func win_achievement_for_animal(animal: String) -> String:
	return "ACH_WIN_%s" % animal.to_upper()

func is_eligible_session() -> bool:
	if not Globals.USE_STEAM_MULTIPLAYER:
		return false
	if Globals.IS_SINGLE_PLAYER or Globals.IS_TUTORIAL_MODE:
		return false
	if not SteamManager.is_steam_initialized:
		return false
	if not multiplayer.has_multiplayer_peer():
		return false
	var mp = SteamMPManager
	if mp == null:
		return false
	var my_id := multiplayer.get_unique_id()
	var human_opponents := 0
	for id in mp.players:
		if id == my_id:
			continue
		if not mp.players[id].get("is_bot", false):
			human_opponents += 1
	return human_opponents >= 1

func reset_session() -> void:
	had_one_territory = false
	session_eligible = is_eligible_session()
	print("AchievementManager: session_eligible=", session_eligible)

func unlock_achievement(api_name: String) -> void:
	if not session_eligible:
		return
	if not SteamManager.is_steam_initialized:
		return
	var data = Steam.getAchievement(api_name)
	if data.get("achieved", false):
		return
	Steam.setAchievement(api_name)
	Steam.storeStats()
	print("AchievementManager: unlocked ", api_name)

func on_combat_result(combat_result: Dictionary) -> void:
	if not session_eligible:
		return
	var defender_conquered: bool = combat_result.get("defender_conquered", false)
	var defender_owner_id: int = int(combat_result.get("defender_owner_id", -1))
	if defender_conquered and defender_owner_id == multiplayer.get_unique_id():
		unlock_achievement("ACH_COUNTER_CAPTURE")
	# Territory ownership syncs after server combat delay (~2s)
	var timer := get_tree().create_timer(2.5)
	timer.timeout.connect(_update_territory_tracking, CONNECT_ONE_SHOT)

func on_local_win(animal: String, _mission: Dictionary) -> void:
	if not session_eligible:
		return
	unlock_achievement("ACH_FIRST_WIN")
	if had_one_territory:
		unlock_achievement("ACH_ATTACK_OF_DESPAIR")
	if animal in SteamMPManager.PLAYER_OPTIONS:
		unlock_achievement(win_achievement_for_animal(animal))

func on_eliminated_human_opponent() -> void:
	unlock_achievement("ACH_ELIMINATE_PLAYER")

func _update_territory_tracking() -> void:
	if not session_eligible or had_one_territory:
		return
	if not Server or not multiplayer.has_multiplayer_peer():
		return
	var count := Server.get_player_territory_count(multiplayer.get_unique_id())
	if count == 1:
		had_one_territory = true
		print("AchievementManager: had_one_territory=true")
