"""Sensors for Eufy X8 Map."""
from __future__ import annotations

import time

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: X8MapCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CurrentRoomSensor(coordinator), RoutePointsSensor(coordinator), MapActivitySensor(coordinator)])


class CurrentRoomSensor(X8MapEntity, SensorEntity):
    """Room id under the robot (rooms are numbered as the X8 numbers them)."""

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "current_room")

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.state
        if state.at_dock(time.monotonic()):
            return "Docked"
        room = state.room_at(state.robot)
        return None if room is None else f"Room {room}"


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
