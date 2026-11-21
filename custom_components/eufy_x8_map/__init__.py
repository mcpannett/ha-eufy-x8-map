"""Eufy X8 Map: live floor plan, robot position and route for the Eufy RoboVac X8."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import CONF_ALLOW_COMMANDS, CONF_LOCAL_LINK, CONTROLS_DEFAULT_APPLIED, DOMAIN
from .coordinator import X8MapCoordinator
from .services import async_register

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BUTTON, Platform.CAMERA, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH, Platform.TEXT, Platform.VACUUM]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not entry.options.get(CONTROLS_DEFAULT_APPLIED):
        # 0.8.0: HA control is the point of the integration, so controls and the LAN link are on by default.
        hass.config_entries.async_update_entry(entry, options={
            **entry.options, CONF_ALLOW_COMMANDS: True, CONF_LOCAL_LINK: True, CONTROLS_DEFAULT_APPLIED: True})
    coordinator = X8MapCoordinator(hass, entry)
    await coordinator.async_load_cache()
    await coordinator.async_load_history()
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register(hass)
    coordinator.start_local()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator:
            await coordinator.stop_local()
    return unloaded

# Options (vacuum entity, recorder) are read live by the coordinator, so no reload is needed.
