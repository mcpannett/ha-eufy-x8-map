"""Sensors for Eufy X8 Map."""
from __future__ import annotations

import time

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ROOM_NAMES, DOCKED_STATUSES, DOMAIN
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity
from .lib.engine import parse_room_names


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: X8MapCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CurrentRoomSensor(coordinator), RoutePointsSensor(coordinator), MapActivitySensor(coordinator),
                        RobotStatusSensor(coordinator), RecordedCommandsSensor(coordinator)])


class CurrentRoomSensor(X8MapEntity, SensorEntity):
    """Room id under the robot (rooms are numbered as the X8 numbers them)."""

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "current_room")

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.state
        # After a restart there is no position until the robot moves; its own status still says where it is.
        if self.coordinator.dps.get("15") in DOCKED_STATUSES or state.at_dock(time.monotonic()):
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
        value = self.coordinator.dps.get("15")
        return value if isinstance(value, str) else None

    @property
    def extra_state_attributes(self) -> dict:
        return {"detail": self.coordinator.dps.get("122")}


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
