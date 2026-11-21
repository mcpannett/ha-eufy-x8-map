"""Sensors for Eufy X8 Map."""
from __future__ import annotations

import time

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfArea, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ROOM_NAMES, DOCKED_STATUSES, DOMAIN
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity
from .lib.engine import parse_room_names


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: X8MapCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CurrentRoomSensor(coordinator), RoutePointsSensor(coordinator), MapActivitySensor(coordinator),
                        RobotStatusSensor(coordinator), RecordedCommandsSensor(coordinator),
                        BatterySensor(coordinator), SuctionSensor(coordinator), CleanModeSensor(coordinator),
                        LocalLinkSensor(coordinator), CleanTimeSensor(coordinator), CleanAreaSensor(coordinator),
                        RunCodeSensor(coordinator)]
                       + [RoomLastCleanedSensor(coordinator, r, parse_room_names(entry.options.get(CONF_ROOM_NAMES)).get(r, f"Room {r}"))
                          for r in coordinator.state.rooms])


class CurrentRoomSensor(X8MapEntity, SensorEntity):
    """Room id under the robot (rooms are numbered as the X8 numbers them)."""

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "current_room")

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.state
        # After a restart there is no position until the robot moves; its own status still says where it is.
        if self.coordinator.dp("15") in DOCKED_STATUSES or state.at_dock(time.monotonic()):
            return "Docked"
        room = state.room_at(state.robot)
        if room is None:
            return None
        return parse_room_names(self.coordinator.entry.options.get(CONF_ROOM_NAMES)).get(room, f"Room {room}")


class RoutePointsSensor(X8MapEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "route_points")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.state.poses)


class MapActivitySensor(X8MapEntity, SensorEntity):
    """'live' while position updates are arriving, otherwise 'idle'."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "map_activity")

    @property
    def native_value(self) -> str:
        return "live" if self.coordinator.state.is_active(time.monotonic()) else "idle"


class RobotStatusSensor(X8MapEntity, SensorEntity):
    """The X8's own work status (DPS 15), e.g. Charging, Running, Recharge."""

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "robot_status")

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.dp("15")
        return value if isinstance(value, str) else None

    @property
    def extra_state_attributes(self) -> dict:
        return {"detail": self.coordinator.dp("122"), "source": "local" if self.coordinator.local_up else "cloud"}


class RecordedCommandsSensor(X8MapEntity, SensorEntity):
    """Count of recorded app commands; the decoded commands are in the attributes."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "recorded_commands")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.recorder.items)

    @property
    def extra_state_attributes(self) -> dict:
        return {"recording": self.coordinator.recording, "history": self.coordinator.history_status,
                "commands": self.coordinator.recorder.items[:25]}


KNOWN_LOCAL_DPS = {"5", "15", "101", "102", "104", "109", "110", "122", "124", "137"}


class _LocalValue(X8MapEntity, SensorEntity):
    _dp = ""

    @property
    def native_value(self):
        return self.coordinator.dp(self._dp)


class BatterySensor(_LocalValue):
    _dp = "104"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "battery")

    @property
    def native_value(self):
        value = self.coordinator.dp(self._dp)
        return value if isinstance(value, (int, float)) else None


class SuctionSensor(_LocalValue):
    _dp = "102"

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "suction")


class CleanModeSensor(_LocalValue):
    _dp = "5"

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "clean_mode")


class LocalLinkSensor(X8MapEntity, SensorEntity):
    """State of the LAN link, plus every other DPS the robot reports (for decoding more sensors)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "local_link_status")

    @property
    def native_value(self) -> str:
        return self.coordinator.local_status

    @property
    def extra_state_attributes(self) -> dict:
        coord = self.coordinator
        link = coord._link  # noqa: SLF001
        seen = None if not link or link.last_seen is None else round(time.monotonic() - link.last_seen)
        return {"host": coord.local_host, "seconds_since_data": seen, "last_send_route": coord.last_route,
                "other_dps": {k: v for k, v in sorted(coord.local_dps.items()) if k not in KNOWN_LOCAL_DPS}}


class _Number(_LocalValue):
    @property
    def native_value(self):
        value = self.coordinator.dp(self._dp)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class CleanTimeSensor(_Number):
    """Duration of the current/last clean (DPS 109)."""

    _dp = "109"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "clean_time")


class CleanAreaSensor(_Number):
    """Area cleaned in the current/last clean (DPS 110)."""

    _dp = "110"
    _attr_device_class = SensorDeviceClass.AREA
    _attr_native_unit_of_measurement = UnitOfArea.SQUARE_METERS

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "clean_area")


class RunCodeSensor(_Number):
    """Raw reason/error code (DPS 137); 24 was seen when a room's door was shut. 0 = fine."""

    _dp = "137"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "run_code")

    @property
    def extra_state_attributes(self) -> dict:
        value = self.native_value
        return {"meaning": RUN_CODES.get(value, "unknown") if value is not None else None}


RUN_CODES = {0: "ok", 24: "room unreachable (seen with a closed door)"}


class RoomLastCleanedSensor(X8MapEntity, SensorEntity):
    """When the robot last cleaned this room (at least 30 s inside it during a run). Kept across restarts."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:history"

    def __init__(self, coordinator: X8MapCoordinator, room: int, name: str) -> None:
        super().__init__(coordinator, f"last_cleaned_{room}")
        self._room = room
        self._attr_translation_key = None
        self._attr_name = f"{name} last cleaned"

    @property
    def native_value(self):
        from datetime import datetime, timezone
        ts = self.coordinator.tracker.last_cleaned.get(self._room)
        return datetime.fromtimestamp(ts, timezone.utc) if ts else None
