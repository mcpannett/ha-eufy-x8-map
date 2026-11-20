"""Refresh-map button for Eufy X8 Map."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([RefreshMapButton(hass.data[DOMAIN][entry.entry_id])])


class RefreshMapButton(X8MapEntity, ButtonEntity):
    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "refresh_map")

    async def async_press(self) -> None:
        self.coordinator.force_map = True
        self.coordinator._map_retry_at = 0.0  # noqa: SLF001
        await self.coordinator.async_request_refresh()
