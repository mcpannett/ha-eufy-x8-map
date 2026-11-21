"""Test-command services for Eufy X8 Map. All refuse unless 'Allow commands' is on."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
import base64

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .const import CONF_ROOM_NAMES, DOCKED_STATUSES, DOMAIN, GOTO_CLEAR_WAIT_S
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


def async_register(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "command"):
        return

    async def command(call: ServiceCall) -> None:
        await _guard(_coordinator(hass).async_command(call.data["method"], call.data.get("data")))

    async def return_home(call: ServiceCall) -> None:
        coord = _coordinator(hass)
        if coord.dps.get("15") in DOCKED_STATUSES:
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
            dps = room_clean_dps(ids, call.data.get("clean_times", DEFAULT_CLEAN_TIMES))
        except CommandRefused as err:
            raise ServiceValidationError(str(err)) from None
        label = "clean rooms " + ", ".join(names.get(i, f"Room {i}") for i in ids)
        await _guard(coord.async_send(dps, label))

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
