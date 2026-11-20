"""Polling for Eufy X8 Map: fast while the robot is cleaning, slow while docked."""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACTIVE_MAP_S, ACTIVE_POLL_S, ACTIVE_VACUUM_STATES, CONF_ACCOUNT, CONF_DEVICE_ID,
    CONF_VACUUM_ENTITY, DOMAIN, IDLE_MAP_S, IDLE_POLL_S,
)
from .lib.engine import Account, MapState, X8Client

_LOGGER = logging.getLogger(__name__)


class X8MapCoordinator(DataUpdateCoordinator[MapState]):
    """Keeps one MapState up to date. Errors are reported by type only (no URLs or tokens)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=IDLE_POLL_S))
        self.entry = entry
        self.client = X8Client(Account(**entry.data[CONF_ACCOUNT]), entry.data[CONF_DEVICE_ID])
        self.state = MapState()

    def _vacuum_active(self) -> bool:
        entity = self.entry.options.get(CONF_VACUUM_ENTITY)
        current = self.hass.states.get(entity) if entity else None
        return current is not None and current.state in ACTIVE_VACUUM_STATES

    async def _async_update_data(self) -> MapState:
        now = time.monotonic()
        try:
            record, frames = await self.hass.async_add_executor_job(self.client.fetch_frames)
            self.state.update_frames(record, frames, now)
            active = self.state.is_active(now) or self._vacuum_active()
            map_age = None if self.state.layout_time is None else now - self.state.layout_time
            if map_age is None or map_age >= (ACTIVE_MAP_S if active else IDLE_MAP_S):
                layout = await self.hass.async_add_executor_job(self.client.fetch_layout)
                self.state.update_layout(layout, now)
        except Exception as err:  # noqa: BLE001 - library errors can carry signed URLs; report the type only
            raise UpdateFailed(f"Eufy cloud read failed ({type(err).__name__})") from None
        self.update_interval = timedelta(seconds=ACTIVE_POLL_S if active else IDLE_POLL_S)
        return self.state
