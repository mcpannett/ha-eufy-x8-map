"""Polling for Eufy X8 Map: fast while the robot is cleaning, slow while docked."""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVE_MAP_S, ACTIVE_POLL_S, ACTIVE_VACUUM_STATES, CONF_ACCOUNT, CONF_DEVICE_ID,
    CONF_ALLOW_COMMANDS, CONF_LOCAL_KEY, CONF_LOCAL_LINK, CONF_RECORD_COMMANDS, CONF_ROBOT_HOST, LOCAL_RETRY_S, CONF_VACUUM_ENTITY, DOMAIN, DPS_POLL_S, IDLE_MAP_S, IDLE_POLL_S,
    RECORD_DPS_POLL_S, RECORD_HISTORY_S, RECORD_MAX_S,
)
from .lib.commands import CommandRecorder, encode_command, history_entries, map_version
from .lib.engine import Account, MapState, X8Client
from .lib.x8map import decode_any
from .lib.history import RunTracker
from .lib.local import LocalLink, discover
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
        self._record_started: float | None = None
        # LAN link (milestone 2). Key lives in memory only.
        self.local_dps: dict = {}
        self.local_status = "off"
        self.local_host: str | None = None
        self.last_route: str | None = None
        self._link: LocalLink | None = None
        self._link_task: asyncio.Task | None = None
        self._decoded = False
        self.selected_rooms: set[int] = set()  # rooms ticked on the card for "Clean selected"
        self.tracker = RunTracker()
        self.last_run: dict = {}
        self._history_store: Store = Store(hass, 1, f"{DOMAIN}.{entry.data[CONF_DEVICE_ID]}.history")
        # Offline copy of the room layout (not secret: floor plan only).
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry.data[CONF_DEVICE_ID]}.layout")
        self._cached_raw: bytes | None = None
        self.map_source: str | None = None
        self.cloud_error: str | None = None

    async def async_load_history(self) -> None:
        data = await self._history_store.async_load() or {}
        self.tracker.last_cleaned = {int(k): v for k, v in (data.get("last_cleaned") or {}).items()}
        self.last_run = data.get("last_run") or {}

    def _track(self) -> None:
        """Run tracking: room history, HA events (eufy_x8_map_event) and stuck/error alerts."""
        state = self.state
        room = None if state.docked else state.room_at(state.robot)
        events = self.tracker.update(self.dp("15"), room, self.dp("106"), time.monotonic(), dt_util.utcnow().timestamp())
        if not events:
            return
        from homeassistant.components import persistent_notification
        from .const import CONF_ROOM_NAMES
        from .lib.engine import parse_room_names
        names = parse_room_names(self.entry.options.get(CONF_ROOM_NAMES))
        save = False
        for event in events:
            data = dict(event)
            if "room" in data:
                data["room_name"] = names.get(data["room"], f"Room {data['room']}")
            if event["type"] == "clean_finished":
                data["room_names"] = [names.get(r, f"Room {r}") for r in event["rooms"]]
                data["area_m2"] = self.dp("110")
                self.last_run = {**data, "finished": dt_util.utcnow().isoformat(timespec="seconds")}
                persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_error")
            if event["type"] in ("room_cleaned", "clean_finished"):
                save = True
            if event["type"] == "error":
                persistent_notification.async_create(
                    self.hass, f"The X8 reported a problem (code {event['code']}). It may be stuck or need help.",
                    title="Eufy X8 needs attention", notification_id=f"{DOMAIN}_error")
            self.hass.bus.async_fire(f"{DOMAIN}_event", data)
        if save:
            self._history_store.async_delay_save(lambda: {
                "last_cleaned": {str(k): v for k, v in self.tracker.last_cleaned.items()}, "last_run": self.last_run}, 5)

    async def async_load_cache(self) -> None:
        """Load the saved layout so the map works even if the cloud is down at start-up."""
        try:
            data = await self._store.async_load()
            if data and data.get("lay"):
                raw = base64.b64decode(data["lay"])
                self.state.update_layout(decode_any(raw), -1e9)  # stale on purpose: refresh when possible
                self._cached_raw = raw
                self.map_source = "saved copy"
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Saved map unreadable (%s); waiting for the cloud", type(err).__name__)

    # --- LAN link -------------------------------------------------------------
    @property
    def local_enabled(self) -> bool:
        return bool(self.entry.options.get(CONF_LOCAL_LINK, True))

    @property
    def local_up(self) -> bool:
        return bool(self._link and self._link.connected)

    def dp(self, key: str):
        """Latest value: from the LAN link when it is up, else from the cloud."""
        if self.local_up and key in self.local_dps:
            return self.local_dps[key]
        return self.dps.get(key, self.local_dps.get(key))

    def _sync_docked(self) -> None:
        from .const import DOCKED_STATUSES
        self.state.set_docked(self.dp("15") in DOCKED_STATUSES)

    def _on_local_dps(self, dps: dict) -> None:
        self._decoded = True
        self.local_dps.update(dps)
        self._sync_docked()
        self._track()
        if self.recording:
            stamp = dt_util.utcnow().isoformat(timespec="seconds")
            for dp, value in dps.items():
                if dp == "124" and isinstance(value, str):
                    self.recorder.add(dp, value, stamp, "local")
        self.async_update_listeners()

    def start_local(self) -> None:
        if self.local_enabled and (self._link_task is None or self._link_task.done()):
            self._link_task = self.entry.async_create_background_task(
                self.hass, self._local_loop(), "eufy_x8_map local link")

    async def stop_local(self) -> None:
        task, self._link_task = self._link_task, None
        if task:
            task.cancel()
        if self._link:
            await self._link.close()
        self._link = None
        self.local_status = "off"
        self.async_update_listeners()

    async def async_get_key(self, refresh: bool = False) -> bytes:
        """LAN key: stored in the config entry so start-up needs no cloud. Refetched only when it stops working."""
        stored = self.entry.data.get(CONF_LOCAL_KEY)
        if stored and not refresh:
            return stored.encode()[:16]
        key = await self.hass.async_add_executor_job(self.client.local_key)
        if key.decode() != stored:
            self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, CONF_LOCAL_KEY: key.decode()})
        return key

    async def _local_loop(self) -> None:
        key: bytes | None = None
        refresh = False
        while self.local_enabled:
            try:
                if key is None or refresh:
                    self.local_status = "getting key"
                    key = await self.async_get_key(refresh)
                    refresh = False
                host = self.entry.options.get(CONF_ROBOT_HOST) or self.local_host
                if not host:
                    self.local_status = "discovering"
                    self.async_update_listeners()
                    host = await discover(self.client.dev_id)
                    if not host:
                        raise ConnectionError("robot not found on the LAN (set its IP in options)")
                self.local_host = host
                self.local_status = "connecting"
                self._link = LocalLink(host, self.client.dev_id, key, self._on_local_dps)
                run = asyncio.ensure_future(self._link.run())
                for _ in range(50):
                    if self._link.connected or run.done():
                        break
                    await asyncio.sleep(0.2)
                if self._link.connected:
                    self.local_status = "connected"
                    _LOGGER.info("LAN link to the robot is up")
                    self.async_update_listeners()
                was_connected, self._decoded = self._link.connected, False
                try:
                    await run
                finally:
                    # Connected but nothing ever decoded: the key has probably changed (e.g. robot re-paired).
                    if was_connected and not self._decoded:
                        refresh = True
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - type/message only, never the key
                self.local_status = f"retrying: {type(err).__name__}: {str(err)[:80]}"
                _LOGGER.debug("LAN link: %s", self.local_status)
            self.async_update_listeners()
            await asyncio.sleep(LOCAL_RETRY_S)

    @property
    def commands_allowed(self) -> bool:
        return bool(self.entry.options.get(CONF_ALLOW_COMMANDS, True))

    async def async_send(self, dps: dict, label: str) -> None:
        """Send DPS through the cloud. Only when 'Allow commands' is on. Logged and recorded."""
        if not self.commands_allowed:
            raise PermissionError("Turn on 'Allow commands' on the map device first")
        if self._map_version is None:
            raise PermissionError("Map version unknown yet; wait a minute after start-up before sending commands")
        self._guard_version = self._map_version
        route = "cloud"
        if self.local_up:
            try:
                await self._link.send(dps)
                route = "local"
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("LAN send failed (%s); using the cloud", type(err).__name__)
        if route == "cloud":
            await self.hass.async_add_executor_job(self.client.publish_dps, dps)
        self.last_route = route
        label = f"{label} (via {route})"
        _LOGGER.info("Sent to robot: %s", label)
        self.recorder.items.insert(0, {"dp": ",".join(dps), "time": dt_util.utcnow().isoformat(timespec="seconds"),
                                       "source": "sent", "label": label})
        del self.recorder.items[self.recorder.max_items:]
        self._last_dps = -1e9  # look for the echo straight away
        self.async_update_listeners()

    async def async_vacuum(self, action: str) -> None:
        """Basic controls. Formats: DPS 101 from the app capture; 2/5/103 from damacus/robovac's X8 (T2262) model."""
        payloads = {
            "start": {"5": "Auto", "2": True},
            "resume": {"2": True},
            "pause": {"2": False},
            "home": {"101": True},
            "locate": {"103": True},
            "spot_here": {"5": "Spot"},
        }
        await self.async_send(payloads[action], action)

    async def async_set_suction(self, level: str) -> None:
        from .const import SUCTION_LEVELS
        if level not in SUCTION_LEVELS:
            raise ValueError(f"unknown suction level {level}")
        await self.async_send({"102": level}, f"suction {level}")

    async def async_clean_rooms(self, room_ids: list[int], clean_times=None) -> None:
        from .lib.commands import DEFAULT_CLEAN_TIMES, room_clean_dps
        from .lib.engine import parse_room_names
        from .const import CONF_ROOM_NAMES
        names = parse_room_names(self.entry.options.get(CONF_ROOM_NAMES))
        skipped = self.closed_rooms(room_ids)
        room_ids = [r for r in room_ids if r not in skipped]
        skipped_names = ", ".join(names.get(r, f"Room {r}") for r in skipped)
        if not room_ids:
            from .lib.commands import CommandRefused
            raise CommandRefused(f"Not cleaning: door closed ({skipped_names})")
        dps = room_clean_dps(room_ids, clean_times or DEFAULT_CLEAN_TIMES)
        await self.async_send(dps, "clean rooms " + ", ".join(names.get(i, f"Room {i}") for i in room_ids)
                              + (f" (skipped, door closed: {skipped_names})" if skipped else ""))
        if skipped:
            from homeassistant.components import persistent_notification
            persistent_notification.async_create(
                self.hass, f"Skipped {skipped_names}: the door is closed. The other rooms are being cleaned.",
                title="Eufy X8", notification_id=f"{DOMAIN}_skipped")

    def door_sensors(self) -> dict[int, str]:
        from .const import CONF_DOOR_SENSORS
        return {int(k): v for k, v in (self.entry.options.get(CONF_DOOR_SENSORS) or {}).items()}

    def closed_rooms(self, room_ids: list[int]) -> list[int]:
        """Rooms whose door sensor says closed ('off'). Unknown/unavailable sensors never block."""
        doors = self.door_sensors()
        closed = []
        for room in room_ids:
            entity = doors.get(room)
            state = self.hass.states.get(entity) if entity else None
            if state is not None and state.state == "off":
                closed.append(room)
        return closed

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
            self.cloud_error = type(err).__name__
            if self.state.layout is None and not self.local_up:
                raise UpdateFailed(f"Eufy cloud read failed ({type(err).__name__})") from None
            # Cloud down: keep going on the saved map and the LAN link.
        else:
            self.cloud_error = None
            self.state.update_frames(record, frames, now)
        await self._poll_dps(now)
        active = self.state.is_active(now) or self._vacuum_active() or self.dp("15") in ("Running", "Recharge", "Locating")
        map_age = None if self.state.layout_time is None else now - self.state.layout_time
        due = map_age is None or map_age >= (ACTIVE_MAP_S if active else IDLE_MAP_S)
        if (self.force_map or due) and now >= self._map_retry_at:
            self.force_map = False
            await self._refresh_map(now)
        self._sync_docked()
        self._track()
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
            from homeassistant.components import persistent_notification
            persistent_notification.async_create(
                self.hass, "The X8's saved map changed after a command from Home Assistant, so HA controls are locked. "
                "Check the map in the Eufy app, then tap 'Unlock controls' on the vac card.",
                title="Eufy X8: controls locked", notification_id=f"{DOMAIN}_guard")
            self.recorder.items.insert(0, {"dp": "125", "time": dt_util.utcnow().isoformat(timespec="seconds"),
                                           "source": "guard", "label": f"map changed {self._guard_version} -> {version}; commands locked"})
            self._guard_version = None
        if version is not None:
            self._map_version = version
        if self.recording:
            if self._record_started is None:
                self._record_started = now
            elif now - self._record_started > RECORD_MAX_S:
                _LOGGER.info("Command recorder switched off after %s h", RECORD_MAX_S // 3600)
                self.hass.config_entries.async_update_entry(
                    self.entry, options={**self.entry.options, CONF_RECORD_COMMANDS: False})
                self._record_started = None
                return
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
            raw = await self.hass.async_add_executor_job(self.client.fetch_layout_raw)
            layout = decode_any(raw)
        except MapFetchError as err:  # our own messages: no URLs, tokens or ids
            error = str(err)
        except Exception as err:  # noqa: BLE001 - other errors may carry URLs; type only
            error = type(err).__name__
        else:
            self.state.update_layout(layout, now)
            self.map_source = "cloud"
            if raw != self._cached_raw:
                self._cached_raw = raw
                await self._store.async_save({"lay": base64.b64encode(raw).decode()})
            if self.map_error:
                _LOGGER.info("Map download working again")
            self.map_error = None
            return
        if error != self.map_error:
            _LOGGER.warning("Map download failed: %s", error)
        self.map_error = error
        self._map_retry_at = now + 60
