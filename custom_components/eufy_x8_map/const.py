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
