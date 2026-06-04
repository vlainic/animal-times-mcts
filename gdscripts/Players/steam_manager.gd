# Players/steam_manager.gd
extends Node

const MAX_LOBBY_MEMBERS = 6  # Match MultiplayerManager.MAX_CLIENTS

# Steam state
var steam_id: int = 0
var steam_username: String = ""
var is_steam_initialized: bool = false

func _ready():
	initialize_steam()

func _process(_delta):
	if is_steam_initialized:
		Steam.run_callbacks()  # Must run every frame

func _read_app_id_from_file() -> String:
	"""Read App ID from steam_appid.txt (res:// in editor, next to exe when exported)."""
	# Editor / dev: project root
	if FileAccess.file_exists("res://steam_appid.txt"):
		var f = FileAccess.open("res://steam_appid.txt", FileAccess.READ)
		if f:
			var content = f.get_as_text().strip_edges()
			f.close()
			return content
	# Exported: next to executable
	var path = OS.get_executable_path().get_base_dir().path_join("steam_appid.txt")
	if FileAccess.file_exists(path):
		var f = FileAccess.open(path, FileAccess.READ)
		if f:
			var content = f.get_as_text().strip_edges()
			f.close()
			return content
	return ""

func initialize_steam() -> bool:
	print("=== INITIALIZING STEAM ===")

	# Read App ID before init (env from Steam, then steam_appid.txt) — store globally for debugging
	var app_id_from_env := OS.get_environment("SteamAppId")
	if app_id_from_env == "":
		app_id_from_env = OS.get_environment("STEAM_APP_ID")
	if app_id_from_env == "":
		app_id_from_env = _read_app_id_from_file()
	if app_id_from_env == "":
		app_id_from_env = "unknown"
	Globals.STEAM_APP_ID_ATTEMPTED = app_id_from_env
	print("App ID (env/file/fallback): ", Globals.STEAM_APP_ID_ATTEMPTED)
	# So Steam SDK uses this when env wasn't set (e.g. dev run)
	if app_id_from_env != "unknown":
		OS.set_environment("SteamAppId", app_id_from_env)

	# Initialize Steam
	var initialize_response: Dictionary = Steam.steamInitEx()

	# Check if initialization was successful
	if initialize_response["status"] != 0:  # 0 = OK, anything else is an error
		push_error("Steam initialization failed: %s" % initialize_response)
		print("ERROR: Steam failed to initialize")
		print("App ID attempted (Globals.STEAM_APP_ID_ATTEMPTED): ", Globals.STEAM_APP_ID_ATTEMPTED)
		print("Make sure Steam client is running!")
		return false

	# Get Steam user info
	steam_id = Steam.getSteamID()
	steam_username = Steam.getPersonaName()
	is_steam_initialized = true

	print("Steam initialized successfully!")
	print("Steam App ID (from Steam): ", Steam.getAppID())
	print("Steam ID: ", steam_id)
	print("Username: ", steam_username)

	# Initialize relay network for P2P
	Steam.initRelayNetworkAccess()

	Steam.requestUserStats(steam_id)

	return true
