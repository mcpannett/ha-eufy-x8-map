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
        self._last_map_request = -1e9
        self.selected_rooms: set[int] = set()  # rooms ticked on the card for "Clean selected"
        self.tracker = RunTracker()
        self.last_run: dict = {}
        self.run_log: list[dict] = []          # finished cleans, newest last (lib.care)
        self._care_alerted: list[str] = []     # consumables already flagged as worn
        self._last_run_code = None
        self._last_care_poll = -1e9
        self._last_care_cloud = -1e9
        self.care_raw = None                  # last good DPS 116 (part wear), kept across restarts
        self.care_run_s = 0.0                 # cleaning time HA has counted since that report
        self.care_source = None               # "robot" or "set in HA"
        self._last_robot_care = None
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
        self.run_log = list(data.get("runs") or [])
        self._care_alerted = list(data.get("care_alerted") or [])
        self.care_raw = data.get("care_raw")
        self.care_run_s = float(data.get("care_run_s") or 0.0)
        self.care_source = data.get("care_source")

    def _save_history(self) -> None:
        self._history_store.async_delay_save(lambda: {
            "last_cleaned": {str(k): v for k, v in self.tracker.last_cleaned.items()}, "last_run": self.last_run,
            "runs": self.run_log, "care_alerted": self._care_alerted,
            "care_raw": self.care_raw, "care_run_s": self.care_run_s, "care_source": self.care_source}, 5)

    @property
    def care_report(self):
        """Part wear now: last report + cleaning time counted by HA since (whole hours)."""
        from .lib.care import with_extra_hours
        return with_extra_hours(self.care_raw, self.care_run_s)

    def set_care_base(self, report, source: str) -> None:
        self.care_raw, self.care_run_s, self.care_source = report, 0.0, source
        self._save_history()
        self.async_update_listeners()

    @property
    def topup_days(self) -> int:
        from .const import CONF_TOPUP_DAYS, DEFAULT_TOPUP_DAYS
        return int(self.entry.options.get(CONF_TOPUP_DAYS, DEFAULT_TOPUP_DAYS))

    def rooms_due(self) -> list[int]:
        from .lib.care import rooms_due
        return rooms_due(self.state.rooms, self.tracker.last_cleaned, self.topup_days, dt_util.utcnow().timestamp())

    def room_names(self) -> dict[int, str]:
        from .const import CONF_ROOM_NAMES
        from .lib.engine import parse_room_names
        return parse_room_names(self.entry.options.get(CONF_ROOM_NAMES))

    def alert(self, message: str, title: str = "Eufy X8 needs attention", key: str = "error", snapshot: bool = True) -> None:
        """HA notification, plus a phone push (with the live map) when a notify service is set."""
        from homeassistant.components import persistent_notification
        from homeassistant.helpers import entity_registry as er
        from .const import CONF_NOTIFY
        persistent_notification.async_create(self.hass, message, title=title, notification_id=f"{DOMAIN}_{key}")
        target = (self.entry.options.get(CONF_NOTIFY) or "").strip()
        if not target.startswith("notify.") or not self.hass.services.has_service("notify", target[7:]):
            return
        data: dict = {"tag": f"{DOMAIN}_{key}", "group": "eufy_x8"}
        camera = er.async_get(self.hass).async_get_entity_id("camera", DOMAIN, f"{self.entry.data[CONF_DEVICE_ID]}_map")
        if snapshot and camera:
            data["image"] = f"/api/camera_proxy/{camera}"
            data["entity_id"] = camera
        self.hass.async_create_task(self.hass.services.async_call(
            "notify", target[7:], {"title": title, "message": message, "data": data}, blocking=False))

    def _check_care(self) -> None:
        """Run-code nudges (door closed) and worn-part reminders. Each fires once per occurrence."""
        from .lib.care import REPLACE_AT_PERCENT, consumables
        code = self.dp("137")
        if code != self._last_run_code:
            if code in (24, "24") and self._last_run_code is not None and self.tracker.running:
                where = self.state.room_at(self.state.robot) if not self.state.docked else None
                near = f" (near {self.room_names().get(where, f'Room {where}')})" if where else ""
                self.alert(f"The X8 can't reach a room{near}. Is a door closed?", key="unreachable")
            self._last_run_code = code
        raw = self.dp("116")
        if raw and consumables(raw) and raw != self._last_robot_care:
            self._last_robot_care = raw
            self.set_care_base(raw, "robot")
        parts = consumables(self.care_report)
        worn = sorted(k for k, v in parts.items() if v["percent"] <= REPLACE_AT_PERCENT)
        new = [k for k in worn if k not in self._care_alerted]
        if new:
            label = ", ".join(k.replace("_", " ") for k in new)
            self.alert(f"Time to replace: {label} (10% or less left).", title="Eufy X8 maintenance", key="care",
                       snapshot=False)
        if set(worn) != set(self._care_alerted):
            self._care_alerted = worn   # a part that's been replaced (back above 10%) can alert again later
            self._save_history()

    def _track(self) -> None:
        """Run tracking: room history, HA events (eufy_x8_map_event) and stuck/error alerts."""
        state = self.state
        room = None if state.docked else state.room_at(state.robot)
        self._check_care()
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
                from .lib.care import add_run
                data["room_names"] = [names.get(r, f"Room {r}") for r in event["rooms"]]
                data["area_m2"] = self.dp("110")
                self.last_run = {**data, "finished": dt_util.utcnow().isoformat(timespec="seconds")}
                self.care_run_s += float(event.get("duration_s") or 0)  # parts wear by run time
                if event["rooms"]:  # a run that never got into a room isn't a clean
                    now_ts = dt_util.utcnow().timestamp()
                    self.run_log = add_run(self.run_log, {**data, "ts": now_ts}, now_ts)
                persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_error")
            if event["type"] in ("room_cleaned", "clean_finished"):
                save = True
            if event["type"] == "error":
                where = None if self.state.docked else self.state.room_at(self.state.robot)
                near = f" in {names.get(where, f'Room {where}')}" if where else ""
                self.alert(f"The X8 reported a problem{near} (code {event['code']}). It may be stuck or need help.")
            self.hass.bus.async_fire(f"{DOMAIN}_event", data)
        if save:
            self._save_history()

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
        self.state.set_docked(self.is_docked)

    @property
    def is_docked(self) -> bool:
        """Charging/Sleeping, or 'completed' (fully charged) when no run is under way."""
        from .const import DOCKED_STATUSES
        status = self.dp("15")
        return status in DOCKED_STATUSES or (status == "completed" and not self.tracker.running)

    def _on_local_dps(self, dps: dict) -> None:
        self._decoded = True
        started = dps.get("15") in ("Running", "Locating") and self.local_dps.get("15") not in ("Running", "Locating")
        self.local_dps.update(dps)
        if started:  # switch to fast polling (and position streaming) straight away
            self.hass.async_create_task(self.async_request_refresh())
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
        return True  # 0.16.1: no lock. the lock/unlock step was removed; the map guard now alerts instead.

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

    async def _keep_position_streaming(self, now: float) -> None:
        """While the robot is cleaning, repeat the app's map request so the live position keeps updating.

        Read-only for the robot (it just uploads its position); sent over the LAN only, not logged as a command."""
        from .const import MAP_REQUEST_S
        from .lib.commands import map_request_dps
        if not (self.local_up and self.commands_allowed and self.dp("15") in ("Running", "Locating", "Recharge")):
            return
        if now - self._last_map_request < MAP_REQUEST_S:
            return
        self._last_map_request = now
        try:
            await self._link.send(map_request_dps(self.client.dev_id))
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Map request not sent: %s", type(err).__name__)

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
        self.selected_rooms.clear()  # picks are for one clean
        self.async_update_listeners()
        if skipped:
            self.alert(f"Skipped {skipped_names}: the door is closed. The other rooms are being cleaned.",
                       title="Eufy X8", key="skipped", snapshot=False)

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
        await self._poll_consumables(now)
        await self._poll_care_cloud(now)
        active = self.state.is_active(now) or self._vacuum_active() or self.dp("15") in ("Running", "Recharge", "Locating")
        map_age = None if self.state.layout_time is None else now - self.state.layout_time
        due = map_age is None or map_age >= (ACTIVE_MAP_S if active else IDLE_MAP_S)
        if (self.force_map or due) and now >= self._map_retry_at:
            self.force_map = False
            await self._refresh_map(now)
        self._sync_docked()
        self._track()
        await self._keep_position_streaming(now)
        self.update_interval = timedelta(seconds=ACTIVE_POLL_S if active else IDLE_POLL_S)
        return self.state

    async def _poll_consumables(self, now: float) -> None:
        """Ask the robot to re-report part wear (DPS 116) every 30 min. Read-only (Tuya UPDATEDPS)."""
        if not self.local_up or now - self._last_care_poll < 1800:
            return
        self._last_care_poll = now
        try:
            await self._link.request_update([116, 125, 142])
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Consumables request failed (%s)", type(err).__name__)

    async def _poll_care_cloud(self, now: float) -> None:
        """Part wear from Tuya's cloud logs: at start-up, then every 6 h. Read-only, never fatal."""
        if now - self._last_care_cloud < 6 * 3600:
            return
        self._last_care_cloud = now
        await self.async_care_from_cloud()

    async def async_care_from_cloud(self) -> list[str]:
        from .lib.care import consumables
        try:
            raw, notes = await self.hass.async_add_executor_job(self.client.fetch_care_report)
        except Exception as err:  # noqa: BLE001
            return [f"failed: {type(err).__name__}"]
        if raw and consumables(raw) and raw != self._last_robot_care:
            self._last_robot_care = raw
            self.set_care_base(raw, "robot")
        return notes

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
            # Safety net: the saved map changed soon after a command from HA. Alert (no lock) so it can be checked.
            _LOGGER.warning("Map changed after a command from HA (%s -> %s)", self._guard_version, version)
            self.alert("The X8's saved map changed just after a command from Home Assistant. "
                       "Check the map in the Eufy app.", title="Eufy X8: map changed", key="guard")
            self.recorder.items.insert(0, {"dp": "125", "time": dt_util.utcnow().isoformat(timespec="seconds"),
                                           "source": "guard", "label": f"map changed {self._guard_version} -> {version}"})
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
