"""Suction level select for Eufy X8 Map (DPS 102)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.const import EntityCategory

from .const import CONF_ORIENTATION, DOMAIN, SUCTION_LEVELS
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SuctionSelect(coordinator), MapLayoutSelect(coordinator)])


class SuctionSelect(X8MapEntity, SelectEntity):
    """Suction power. Same DPS and values as the robovac integration's fan speed."""

    _attr_options = SUCTION_LEVELS
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "suction_level")

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.dp("102")
        return value if value in SUCTION_LEVELS else None

    async def async_select_option(self, option: str) -> None:
        try:
            await self.coordinator.async_set_suction(option)
        except PermissionError as err:
            raise ServiceValidationError(str(err)) from None
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Send failed ({type(err).__name__})") from None


class MapLayoutSelect(X8MapEntity, SelectEntity):
    """How the map image is drawn: as in the Eufy app (tall), or turned on its side (wide)."""

    _attr_options = ["app", "landscape_right", "landscape_left", "stored"]
    _attr_icon = "mdi:phone-rotate-landscape"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "map_layout")

    @property
    def current_option(self) -> str | None:
        return self.coordinator.entry.options.get(CONF_ORIENTATION, "app")

    async def async_select_option(self, option: str) -> None:
        entry = self.coordinator.entry
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ORIENTATION: option})
        self.coordinator.async_update_listeners()
