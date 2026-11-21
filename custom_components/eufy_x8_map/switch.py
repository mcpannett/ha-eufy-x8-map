"""Command-recorder switch for Eufy X8 Map (diagnostic, off by default)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ALLOW_COMMANDS, CONF_LOCAL_LINK, CONF_RECORD_COMMANDS, CONF_ROOM_NAMES, DOMAIN
from .lib.engine import parse_room_names
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    names = parse_room_names(entry.options.get(CONF_ROOM_NAMES))
    async_add_entities([RecordCommandsSwitch(coordinator), LocalLinkSwitch(coordinator)]
                       + [RoomSelectSwitch(coordinator, r, names.get(r, f"Room {r}")) for r in coordinator.state.rooms])


class RecordCommandsSwitch(X8MapEntity, SwitchEntity):
    """Records the commands the Eufy app sends (read-only; sends nothing to the robot)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "record_commands")

    @property
    def is_on(self) -> bool:
        return self.coordinator.recording

    async def _set(self, value: bool) -> None:
        entry = self.coordinator.entry
        # Options change without a reload, so recorded commands are kept.
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_RECORD_COMMANDS: value})
        self.coordinator._last_dps = -1e9  # noqa: SLF001 - poll straight away
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class AllowCommandsSwitch(X8MapEntity, SwitchEntity):
    """Gate for the test-command services. Off by default; nothing is ever sent while off."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "allow_commands")

    @property
    def is_on(self) -> bool:
        return self.coordinator.commands_allowed

    async def _set(self, value: bool) -> None:
        entry = self.coordinator.entry
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_COMMANDS: value})
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class LocalLinkSwitch(X8MapEntity, SwitchEntity):
    """LAN link to the robot: live status and local commands (cloud stays as fallback). Off by default."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "local_link")

    @property
    def is_on(self) -> bool:
        return self.coordinator.local_enabled

    async def _set(self, value: bool) -> None:
        entry = self.coordinator.entry
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_LOCAL_LINK: value})
        if value:
            self.coordinator.start_local()
        else:
            await self.coordinator.stop_local()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class RoomSelectSwitch(X8MapEntity, SwitchEntity):
    """Tick a room for the next clean. HA-side only (sends nothing to the robot); cleared after each clean and on restart."""

    _attr_icon = "mdi:checkbox-marked-circle-outline"

    def __init__(self, coordinator: X8MapCoordinator, room: int, name: str) -> None:
        super().__init__(coordinator, f"select_room_{room}")
        self._room = room
        self._attr_translation_key = None
        self._attr_name = f"Select {name}"

    @property
    def is_on(self) -> bool:
        return self._room in self.coordinator.selected_rooms

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.selected_rooms.add(self._room)
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.selected_rooms.discard(self._room)
        self.coordinator.async_update_listeners()
