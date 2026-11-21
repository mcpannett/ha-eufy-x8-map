"""Refresh-map button for Eufy X8 Map."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import CONF_ROOM_NAMES, DOMAIN
from .lib.engine import parse_room_names
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: X8MapCoordinator = hass.data[DOMAIN][entry.entry_id]
    names = parse_room_names(entry.options.get(CONF_ROOM_NAMES))
    entities: list[ButtonEntity] = [RefreshMapButton(coordinator)]
    entities += [CleanRoomButton(coordinator, room, names.get(room, f"Room {room}")) for room in coordinator.state.rooms]
    entities += [CleanSelectedButton(coordinator), CleanAllButton(coordinator)]
    async_add_entities(entities)


class RefreshMapButton(X8MapEntity, ButtonEntity):
    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "refresh_map")

    async def async_press(self) -> None:
        self.coordinator.force_map = True
        self.coordinator._map_retry_at = 0.0  # noqa: SLF001
        await self.coordinator.async_request_refresh()


class CleanRoomButton(X8MapEntity, ButtonEntity):
    """Clean one room (two passes by default). Needs 'Allow commands' on."""

    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, coordinator: X8MapCoordinator, room: int, name: str) -> None:
        super().__init__(coordinator, f"clean_room_{room}")
        self._room = room
        self._attr_translation_key = None
        self._attr_name = f"Clean {name}"

    async def async_press(self) -> None:
        if self._room not in self.coordinator.state.rooms:
            raise ServiceValidationError("This room is no longer on the map")
        try:
            await self.coordinator.async_clean_rooms([self._room])
        except (PermissionError, ValueError) as err:
            raise ServiceValidationError(str(err)) from None
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Send failed ({type(err).__name__})") from None


async def _clean(coordinator: X8MapCoordinator, rooms: list[int]) -> None:
    rooms = [r for r in rooms if r in coordinator.state.rooms]
    if not rooms:
        raise ServiceValidationError("No rooms selected - tick at least one room first")
    try:
        await coordinator.async_clean_rooms(sorted(rooms))
    except PermissionError as err:
        raise ServiceValidationError(str(err)) from None
    except Exception as err:  # noqa: BLE001
        raise HomeAssistantError(f"Send failed ({type(err).__name__})") from None


class CleanSelectedButton(X8MapEntity, ButtonEntity):
    """Clean the ticked rooms in one run (two passes), then clear the ticks."""

    _attr_icon = "mdi:playlist-check"

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "clean_selected")

    async def async_press(self) -> None:
        await _clean(self.coordinator, list(self.coordinator.selected_rooms))


class CleanAllButton(X8MapEntity, ButtonEntity):
    """Clean every room on the map in one run (two passes)."""

    _attr_icon = "mdi:home-floor-g"

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "clean_all")

    async def async_press(self) -> None:
        await _clean(self.coordinator, list(self.coordinator.state.rooms))
