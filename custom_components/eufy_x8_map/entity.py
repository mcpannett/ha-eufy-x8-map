"""Shared entity base for Eufy X8 Map."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, CONF_DEVICE_NAME, CONF_MODEL, DOMAIN
from .coordinator import X8MapCoordinator


class X8MapEntity(CoordinatorEntity[X8MapCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: X8MapCoordinator, key: str) -> None:
        super().__init__(coordinator)
        data = coordinator.entry.data
        self._attr_unique_id = f"{data[CONF_DEVICE_ID]}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, data[CONF_DEVICE_ID])},
            name=f"{data[CONF_DEVICE_NAME]} map", manufacturer="Eufy", model=data[CONF_MODEL] or "RoboVac X8")
