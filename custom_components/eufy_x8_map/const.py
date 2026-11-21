"""Constants for Eufy X8 Map."""
DOMAIN = "eufy_x8_map"

CONF_ACCOUNT = "account"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_MODEL = "model"
CONF_VACUUM_ENTITY = "vacuum_entity"

ACTIVE_POLL_S = 2          # robot position while cleaning
IDLE_POLL_S = 60           # check for a new clean while docked
ACTIVE_MAP_S = 20          # map refresh while cleaning
IDLE_MAP_S = 3600          # map refresh while docked (also refreshed when the map version changes)
ACTIVE_VACUUM_STATES = {"cleaning", "returning"}

CONF_RECORD_COMMANDS = "record_commands"
DPS_POLL_S = 60            # robot state + map version
RECORD_DPS_POLL_S = 15     # while the command recorder is on
RECORD_HISTORY_S = 300     # cloud DP history while the recorder is on
CONF_ROOM_NAMES = "room_names"      # "1=Living Room, 2=Hallway"
CONF_ORIENTATION = "orientation"    # "stored" | "app" (matches the Eufy app: transposed)
DOCKED_STATUSES = {"Charging", "Sleeping"}
CONF_ALLOW_COMMANDS = "allow_commands"  # test-command layer gate, off by default
GOTO_CLEAR_WAIT_S = 35                  # the app sends "clear" ~35 s before "goto"
RECORD_MAX_S = 6 * 3600       # recorder turns itself off after this
CONF_LOCAL_LINK = "local_link"          # LAN link to the robot (status + commands), off by default
CONF_ROBOT_HOST = "robot_host"          # optional fixed IP; otherwise found by UDP discovery
LOCAL_RETRY_S = 30                      # wait before reconnecting the LAN link
CONF_LOCAL_KEY = "local_key"            # robot LAN key, stored in the config entry (Matt approved 21 Nov 2026); never logged or shown
CONTROLS_DEFAULT_APPLIED = "controls_default_applied"  # one-off: Allow commands + Local link on by default (0.8.0)
SUCTION_LEVELS = ["Quiet", "Standard", "Turbo", "Max"]  # DPS 102 (damacus/robovac T2262)
