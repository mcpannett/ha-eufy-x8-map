"""Top-up period for Eufy X8 Map: a room is 'due' when it hasn't been cleaned for this many days."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_TOPUP_DAYS, DOMAIN
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([TopUpDaysNumber(hass.data[DOMAIN][entry.entry_id])])


class TopUpDaysNumber(X8MapEntity, NumberEntity):
    _attr_icon = "mdi:calendar-range"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 1
    _attr_native_max_value = 30
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.DAYS

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "topup_days")

    @property
    def native_value(self) -> float:
        return self.coordinator.topup_days

    async def async_set_native_value(self, value: float) -> None:
        entry = self.coordinator.entry
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_TOPUP_DAYS: int(value)})
        self.coordinator.async_update_listeners()
