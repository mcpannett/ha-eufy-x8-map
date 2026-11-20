"""Polling for Eufy X8 Map: fast while the robot is cleaning, slow while docked."""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVE_MAP_S, ACTIVE_POLL_S, ACTIVE_VACUUM_STATES, CONF_ACCOUNT, CONF_DEVICE_ID,
    CONF_ALLOW_COMMANDS, CONF_RECORD_COMMANDS, CONF_VACUUM_ENTITY, DOMAIN, DPS_POLL_S, IDLE_MAP_S, IDLE_POLL_S,
    RECORD_DPS_POLL_S, RECORD_HISTORY_S,
)
from .lib.commands import CommandRecorder, encode_command, history_entries, map_version
from .lib.engine import Account, MapState, X8Client
from .lib.map_fetch import MapFetchError

_LOGGER = logging.getLogger(__name__)


class X8MapCoordinator(DataUpdateCoordinator[MapState]):
    """Keeps one MapState up to date. Errors are reported by type only (no URLs or tokens)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=IDLE_POLL_S))
        self.entry = entry
        self.client = X8Client(Account(**entry.data[CONF_ACCOUNT]), entry.data[CONF_DEVICE_ID])
        self.state = MapState()
        self.map_error: str | None = None
        self._map_retry_at = 0.0
        self.force_map = False
        self.dps: dict = {}
        self.recorder = CommandRecorder()
        self.history_status: str | None = None
        self._last_dps = -1e9
        self._last_history = -1e9
        self._map_version = None
        self._guard_version = None

    @property
    def commands_allowed(self) -> bool:
        return bool(self.entry.options.get(CONF_ALLOW_COMMANDS))

    async def async_send(self, dps: dict, label: str) -> None:
        """Send DPS through the cloud. Only when 'Allow commands' is on. Logged and recorded."""
        if not self.commands_allowed:
            raise PermissionError("Turn on 'Allow commands' on the map device first")
        if self._map_version is None:
            raise PermissionError("Map version unknown yet; wait a minute after start-up before sending commands")
        self._guard_version = self._map_version
        await self.hass.async_add_executor_job(self.client.publish_dps, dps)
        _LOGGER.info("Sent to robot: %s", label)
        self.recorder.items.insert(0, {"dp": ",".join(dps), "time": dt_util.utcnow().isoformat(timespec="seconds"),
                                       "source": "sent", "label": label})
        del self.recorder.items[self.recorder.max_items:]
        self._last_dps = -1e9  # look for the echo straight away
        self.async_update_listeners()

    async def async_command(self, method: str, data=None) -> None:
        await self.async_send({"124": encode_command(method, data)}, f"{method} {data if data is not None else ''}".strip())

    @property
    def recording(self) -> bool:
        return bool(self.entry.options.get(CONF_RECORD_COMMANDS))

    def _vacuum_active(self) -> bool:
        entity = self.entry.options.get(CONF_VACUUM_ENTITY)
        current = self.hass.states.get(entity) if entity else None
        return current is not None and current.state in ACTIVE_VACUUM_STATES

    async def _async_update_data(self) -> MapState:
        now = time.monotonic()
        try:
            record, frames = await self.hass.async_add_executor_job(self.client.fetch_frames)
        except Exception as err:  # noqa: BLE001 - library errors can carry signed URLs; report the type only
            raise UpdateFailed(f"Eufy cloud read failed ({type(err).__name__})") from None
        self.state.update_frames(record, frames, now)
        await self._poll_dps(now)
        active = self.state.is_active(now) or self._vacuum_active()
        map_age = None if self.state.layout_time is None else now - self.state.layout_time
        due = map_age is None or map_age >= (ACTIVE_MAP_S if active else IDLE_MAP_S)
        if (self.force_map or due) and now >= self._map_retry_at:
            self.force_map = False
            await self._refresh_map(now)
        self.update_interval = timedelta(seconds=ACTIVE_POLL_S if active else IDLE_POLL_S)
        return self.state

    async def _poll_dps(self, now: float) -> None:
        """Robot state + map version (and, when recording, app commands). Never fatal."""
        if now - self._last_dps < (RECORD_DPS_POLL_S if self.recording else DPS_POLL_S):
            return
        self._last_dps = now
        try:
            self.dps = await self.hass.async_add_executor_job(self.client.fetch_dps)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("DPS read failed: %s", type(err).__name__)
            return
        version = map_version(self.dps)
        if version is not None and self._map_version is not None and version != self._map_version:
            _LOGGER.info("Map edited in the app; refreshing")
            self.force_map = True
            self._map_retry_at = 0.0
        if version is not None and self._guard_version is not None and version != self._guard_version \
                and self.commands_allowed:
            # Safety: a test command was followed by a map change. Lock commands until a person turns them back on.
            _LOGGER.warning("Map changed after a test command (%s -> %s): commands locked", self._guard_version, version)
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, CONF_ALLOW_COMMANDS: False})
            self.recorder.items.insert(0, {"dp": "125", "time": dt_util.utcnow().isoformat(timespec="seconds"),
                                           "source": "guard", "label": f"map changed {self._guard_version} -> {version}; commands locked"})
            self._guard_version = None
        if version is not None:
            self._map_version = version
        if self.recording:
            stamp = dt_util.utcnow().isoformat(timespec="seconds")
            for dp in ("124", "125", "142"):
                if dp in self.dps:
                    self.recorder.add(dp, self.dps[dp], stamp, "state")
            if now - self._last_history >= RECORD_HISTORY_S:
                self._last_history = now
                await self._poll_history()

    async def _poll_history(self) -> None:
        end = int(dt_util.utcnow().timestamp() * 1000)
        action, result, errors = await self.hass.async_add_executor_job(
            self.client.fetch_dp_history, end - 48 * 3600 * 1000, end)
        if action is None:
            self.history_status = "unavailable: " + "; ".join(errors)
            return
        entries = history_entries(result)
        added = sum(self.recorder.add(e["dp"], e["value"], e["t"], "history") for e in entries)
        self.history_status = f"ok via {action}: {len(entries)} entries, {added} new"

    async def _refresh_map(self, now: float) -> None:
        """Map failures never stop the integration: the position keeps working and the reason is shown."""
        try:
            layout = await self.hass.async_add_executor_job(self.client.fetch_layout)
        except MapFetchError as err:  # our own messages: no URLs, tokens or ids
            error = str(err)
        except Exception as err:  # noqa: BLE001 - other errors may carry URLs; type only
            error = type(err).__name__
        else:
            self.state.update_layout(layout, now)
            if self.map_error:
                _LOGGER.info("Map download working again")
            self.map_error = None
            return
        if error != self.map_error:
            _LOGGER.warning("Map download failed: %s", error)
        self.map_error = error
        self._map_retry_at = now + 60
