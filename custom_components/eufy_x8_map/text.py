"""Room name boxes for Eufy X8 Map. Names live in HA only (the Eufy app keeps its own labels in its cloud)."""
from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ROOM_NAMES, DOMAIN
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity
from .lib.engine import parse_room_names, rename_room


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: X8MapCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RoomNameText(coordinator, room) for room in coordinator.state.rooms])


def apply_rename(hass: HomeAssistant, coordinator: X8MapCoordinator, room: int, name: str) -> None:
    """Save the new name and reload so buttons, switches and sensors pick it up."""
    entry = coordinator.entry
    text = rename_room(entry.options.get(CONF_ROOM_NAMES), room, name)
    if text == (entry.options.get(CONF_ROOM_NAMES) or ""):
        return
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ROOM_NAMES: text})
    hass.config_entries.async_schedule_reload(entry.entry_id)


class RoomNameText(X8MapEntity, TextEntity):
    """The HA name for one room. Clear it to go back to 'Room N'."""

    _attr_icon = "mdi:form-textbox"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_max = 40

    def __init__(self, coordinator: X8MapCoordinator, room: int) -> None:
        super().__init__(coordinator, f"room_name_{room}")
        self._room = room
        self._attr_translation_key = None
        self._attr_name = f"Room {room} name"

    @property
    def native_value(self) -> str:
        return parse_room_names(self.coordinator.entry.options.get(CONF_ROOM_NAMES)).get(self._room, f"Room {self._room}")

    async def async_set_value(self, value: str) -> None:
        apply_rename(self.hass, self.coordinator, self._room, value)
