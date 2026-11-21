"""Test-command services for Eufy X8 Map. All refuse unless 'Allow commands' is on."""
from __future__ import annotations

import asyncio
import json
import logging
import time

import voluptuous as vol
import base64

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .const import CONF_ROOM_NAMES, DOMAIN, GOTO_CLEAR_WAIT_S
from .coordinator import X8MapCoordinator
from .lib.commands import DEFAULT_CLEAN_TIMES, CommandRefused, resolve_rooms, room_clean_dps, spot_clean_dps
from .lib.engine import parse_room_names

_LOGGER = logging.getLogger(__name__)


def _coordinator(hass: HomeAssistant) -> X8MapCoordinator:
    coordinators = list(hass.data.get(DOMAIN, {}).values())
    if not coordinators:
        raise ServiceValidationError("Eufy X8 Map is not set up")
    return coordinators[0]


async def _guard(coro) -> None:
    try:
        await coro
    except PermissionError as err:
        raise ServiceValidationError(str(err)) from None
    except CommandRefused as err:
        raise ServiceValidationError(str(err)) from None
    except Exception as err:  # noqa: BLE001 - may carry URLs; type only
        raise HomeAssistantError(f"Send failed ({type(err).__name__})") from None


def _short(value, depth: int = 0):
    """Trim long strings (map blobs) for the response; decode base64 JSON where possible."""
    if isinstance(value, dict):
        return {str(k): _short(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_short(v, depth + 1) for v in value[:20]]
    if isinstance(value, str):
        if len(value) >= 8:
            try:
                return {"b64json": _short(json.loads(base64.b64decode(value, validate=True).decode()), depth + 1)}
            except Exception:  # noqa: BLE001
                pass
        return value if len(value) <= 300 else {"len": len(value), "head": value[:200]}
    return value


def async_register(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "command"):
        return

    async def command(call: ServiceCall) -> None:
        await _guard(_coordinator(hass).async_command(call.data["method"], call.data.get("data")))

    async def return_home(call: ServiceCall) -> None:
        coord = _coordinator(hass)
        if coord.is_docked:
            raise ServiceValidationError("The robot is already docked")
        await _guard(coord.async_send({"101": True}, "return_home"))

    async def goto(call: ServiceCall) -> None:
        # Disabled after 20 Nov 2026: a goto in unverified coordinates made the X8 lose its room map.
        # Stays off until the goto coordinate system is confirmed from a capture of the Eufy app.
        raise ServiceValidationError("goto is disabled until its coordinate system is verified from an app capture")
        coord = _coordinator(hass)  # noqa: F841 - kept for when goto is re-enabled
        if "x" in call.data:
            target = (int(call.data["x"]), int(call.data["y"]))
        else:
            pose = coord.state.to_pose((float(call.data["pixel_x"]), float(call.data["pixel_y"])))
            if pose is None:
                raise ServiceValidationError("No map yet")
            target = pose
        await _guard(coord.async_command("clear"))

        async def later() -> None:
            await asyncio.sleep(GOTO_CLEAR_WAIT_S)
            try:
                await coord.async_command("goto", {"target": "go", "x": target[0], "y": target[1]})
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("goto not sent: %s", type(err).__name__)
        hass.async_create_background_task(later(), "eufy_x8_map goto")

    async def clean_rooms(call: ServiceCall) -> None:
        coord = _coordinator(hass)
        state = coord.state
        if not state.layout:
            raise ServiceValidationError("No map yet")
        names = parse_room_names(coord.entry.options.get(CONF_ROOM_NAMES))
        try:
            ids = resolve_rooms(call.data["rooms"], names, state.rooms)
            room_clean_dps(ids, call.data.get("clean_times", DEFAULT_CLEAN_TIMES))  # validates
        except CommandRefused as err:
            raise ServiceValidationError(str(err)) from None
        await _guard(coord.async_clean_rooms(ids, call.data.get("clean_times", DEFAULT_CLEAN_TIMES)))

    async def spot_clean(call: ServiceCall) -> None:
        coord = _coordinator(hass)
        state = coord.state
        if not state.layout:
            raise ServiceValidationError("No map yet")
        if "x" in call.data:
            pose = (int(call.data["x"]), int(call.data["y"]))
            pixel = state.to_pixel(pose)
        else:
            pixel = (float(call.data["pixel_x"]), float(call.data["pixel_y"]))
            pose = state.to_pose(pixel)
        room = state.room_at(pixel)
        if pose is None or room is None:
            raise ServiceValidationError("That point is not inside a room on the current map")
        try:
            dps = spot_clean_dps(pose[0], pose[1], call.data.get("clean_times", DEFAULT_CLEAN_TIMES))
        except CommandRefused as err:
            raise ServiceValidationError(str(err)) from None
        await _guard(coord.async_send(dps, f"spot clean at {pose[0]},{pose[1]} (room {room})"))

    async def local_probe(call: ServiceCall) -> ServiceResponse:
        """Read-only: discover the robot and read its DPS once over the LAN. Sends no control commands."""
        from .lib.local import LocalLink, discover
        coord = _coordinator(hass)
        host = call.data.get("host") or coord.local_host
        discovered = None
        if not host:
            discovered = await discover(coord.client.dev_id)
            host = discovered
        if not host:
            return {"ok": False, "error": "robot not found by discovery; pass host"}
        got: dict = {}
        try:
            key = await coord.async_get_key()
            link = LocalLink(host, coord.client.dev_id, key, got.update)
            run = asyncio.ensure_future(link.run())
            for _ in range(40):
                if got or run.done():
                    break
                await asyncio.sleep(0.25)
            run.cancel()
            await link.close()
        except Exception as err:  # noqa: BLE001
            return {"ok": False, "host": host, "error": f"{type(err).__name__}: {str(err)[:80]}"}
        if got:
            coord.local_host = host
        return {"ok": bool(got), "host": host, "discovered": discovered, "dps": got}

    async def local_listen(call: ServiceCall) -> ServiceResponse:
        """Research (milestone 3): ask the robot to re-report DPS over the LAN and, optionally, send the
        app's own map request (DPS 121, format from the 21 Nov capture). Returns everything heard."""
        coord = _coordinator(hass)
        link = coord._link  # noqa: SLF001
        if not coord.local_up or link is None:
            raise ServiceValidationError("Turn on 'Local link' first (status must be connected)")
        heard: list = []
        start = time.monotonic()

        def listener(cmd: int, msg: dict) -> None:
            heard.append({"s": round(time.monotonic() - start, 1), "cmd": cmd, "msg": _short(msg)})
        link.listeners.append(listener)
        try:
            if call.data.get("update_dps"):
                await link.request_update(call.data["update_dps"])
            if call.data.get("map_request"):
                if not coord.commands_allowed:
                    raise ServiceValidationError("map_request sends DPS 121: turn on 'Allow commands' first")
                value = base64.b64encode(json.dumps(
                    {"type": "mapData", "id": call.data.get("request_id", "bfb4b641c939046390ios5"),
                     "timestamp": round(time.time() * 1000)}, separators=(",", ":")).encode()).decode()
                await link.send({"121": value})
            await asyncio.sleep(call.data.get("seconds", 20))
        finally:
            link.listeners.remove(listener)
        return {"heard": heard, "count": len(heard)}

    async def set_door_sensors(call: ServiceCall) -> None:
        """Map rooms to door sensors: {"Bathroom": "binary_sensor.bathroom_door"}. Empty value removes."""
        from .const import CONF_DOOR_SENSORS
        coord = _coordinator(hass)
        names = parse_room_names(coord.entry.options.get(CONF_ROOM_NAMES))
        doors = {str(k): v for k, v in coord.door_sensors().items()}
        for room, entity in call.data["doors"].items():
            try:
                rid = resolve_rooms([room], names, coord.state.rooms)[0]
            except CommandRefused as err:
                raise ServiceValidationError(str(err)) from None
            if entity:
                if not str(entity).startswith("binary_sensor.") or hass.states.get(entity) is None:
                    raise ServiceValidationError(f"{entity} is not a binary sensor in this HA")
                doors[str(rid)] = entity
            else:
                doors.pop(str(rid), None)
        hass.config_entries.async_update_entry(coord.entry, options={**coord.entry.options, CONF_DOOR_SENSORS: doors})

    async def map_card_config(call: ServiceCall) -> ServiceResponse:
        """Read-only: a ready-made config for the Xiaomi Vacuum Map card (tap rooms, tap a spot)."""
        from homeassistant.helpers import entity_registry as er
        from .lib.render import room_shapes
        coord = _coordinator(hass)
        reg = er.async_get(hass)
        dev = coord.client.dev_id
        vac = reg.async_get_entity_id("vacuum", DOMAIN, f"{dev}_vacuum")
        cam = reg.async_get_entity_id("camera", DOMAIN, f"{dev}_map")
        names = parse_room_names(coord.entry.options.get(CONF_ROOM_NAMES))
        rooms = []
        for rid, shape in sorted(room_shapes(coord.state).items()):
            item = {"id": str(rid), "outline": shape["outline"],
                    "label": {"text": names.get(rid, f"Room {rid}"), "x": shape["centre"][0], "y": shape["centre"][1]}}
            sw = reg.async_get_entity_id("switch", DOMAIN, f"{dev}_select_room_{rid}")
            if sw:
                item["state_entity"] = sw
            rooms.append(item)
        card = {
            "type": "custom:xiaomi-vacuum-map-card",
            "entity": vac,
            "vacuum_platform": "default",
            "map_source": {"camera": cam},
            "calibration_source": {"camera": True},
            "map_locked": True,
            # Keep the bar simple: play (smart: picked rooms, else everything), pause, home, fan speed.
            "append_icons": True,
            "icons": [{"icon_id": "start_cleanup", "replace_config": True, "icon": "mdi:home-floor-g",
                       "tooltip": "Clean whole downstairs",
                       "tap_action": {"action": "call-service", "service": "button.press",
                                      "service_data": {"entity_id": reg.async_get_entity_id(
                                          "button", DOMAIN, f"{dev}_clean_all")},
                                      "confirmation": {"text": "Clean every room (2 passes)? Rooms with a closed door are skipped."}}}]
                     + [{"icon_id": i, "remove": True, "replace_config": True} for i in ("stop", "clean_spot", "locate")],
            "tiles": [{"entity": reg.async_get_entity_id("sensor", DOMAIN, f"{dev}_robot_status"), "label": "Status",
                       "icon": "mdi:robot-vacuum"}],
            "map_modes": [
                {"name": "Rooms", "icon": "mdi:floor-plan", "selection_type": "ROOM", "max_selections": 10,
                 "coordinates_rounding": True,
                 "service_call_schema": {"service": f"{DOMAIN}.clean_rooms", "service_data": {"rooms": "[[selection]]"}},
                 "predefined_selections": rooms},
                {"name": "Spot", "icon": "mdi:target", "selection_type": "MANUAL_POINT", "coordinates_rounding": True,
                 "service_call_schema": {"service": f"{DOMAIN}.spot_clean",
                                         "service_data": {"x": "[[point_x]]", "y": "[[point_y]]"}}},
            ],
        }
        return {"card": card}

    async def decode_capture(call: ServiceCall) -> ServiceResponse:
        """Read-only: decrypt captured Eufy-app traffic inside HA (the robot key never leaves HA)."""
        try:
            flows = [{"to_robot": bool(f["to_robot"]), "t": f.get("t"), "data": base64.b64decode(f["data"])}
                     for f in call.data["flows"]]
        except Exception:  # noqa: BLE001
            raise ServiceValidationError("flows must be a list of {to_robot, t, data(base64)}") from None
        coord = _coordinator(hass)
        try:
            messages = await hass.async_add_executor_job(coord.client.decode_flows, flows)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Decode failed ({type(err).__name__})") from None
        return {"messages": messages, "count": len(messages)}

    hass.services.async_register(DOMAIN, "local_probe", local_probe, schema=vol.Schema({
        vol.Optional("host"): cv.string}), supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "local_listen", local_listen, schema=vol.Schema({
        vol.Optional("seconds", default=20): vol.All(vol.Coerce(int), vol.Range(min=2, max=40)),
        vol.Optional("update_dps", default=[116, 121, 125, 142]): [vol.Coerce(int)],
        vol.Optional("map_request", default=False): cv.boolean,
        vol.Optional("request_id"): cv.string}), supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "set_door_sensors", set_door_sensors, schema=vol.Schema({
        vol.Required("doors"): {cv.string: vol.Any(cv.string, None)}}))
    hass.services.async_register(DOMAIN, "map_card_config", map_card_config, schema=vol.Schema({}),
                                 supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "decode_capture", decode_capture, schema=vol.Schema({
        vol.Required("flows"): list}), supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "command", command, schema=vol.Schema({
        vol.Required("method"): cv.string, vol.Optional("data"): vol.Any(dict, list, str, int, float, bool)}))
    hass.services.async_register(DOMAIN, "clean_rooms", clean_rooms, schema=vol.Schema({
        vol.Required("rooms"): vol.Any(cv.string, int, [vol.Any(cv.string, int)]),
        vol.Optional("clean_times", default=DEFAULT_CLEAN_TIMES): vol.All(vol.Coerce(int), vol.Range(min=1, max=3))}))
    hass.services.async_register(DOMAIN, "spot_clean", spot_clean, schema=vol.All(vol.Schema({
        vol.Inclusive("x", "robot"): vol.Coerce(int), vol.Inclusive("y", "robot"): vol.Coerce(int),
        vol.Inclusive("pixel_x", "pixel"): vol.Coerce(float), vol.Inclusive("pixel_y", "pixel"): vol.Coerce(float),
        vol.Optional("clean_times", default=DEFAULT_CLEAN_TIMES): vol.All(vol.Coerce(int), vol.Range(min=1, max=3))}),
        cv.has_at_least_one_key("x", "pixel_x")))
    hass.services.async_register(DOMAIN, "return_home", return_home, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, "goto", goto, schema=vol.All(vol.Schema({
        vol.Inclusive("x", "robot"): vol.Coerce(int), vol.Inclusive("y", "robot"): vol.Coerce(int),
        vol.Inclusive("pixel_x", "pixel"): vol.Coerce(float), vol.Inclusive("pixel_y", "pixel"): vol.Coerce(float)}),
        cv.has_at_least_one_key("x", "pixel_x")))
