"""Command-recorder switch for Eufy X8 Map (diagnostic, off by default)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_RECORD_COMMANDS, DOMAIN
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([RecordCommandsSwitch(hass.data[DOMAIN][entry.entry_id])])


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
