"""Test-command services for Eufy X8 Map. All refuse unless 'Allow commands' is on."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, GOTO_CLEAR_WAIT_S
from .coordinator import X8MapCoordinator
from .lib.commands import CommandRefused

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
        await _guard(_coordinator(hass).async_send({"101": True}, "return_home"))

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

    hass.services.async_register(DOMAIN, "command", command, schema=vol.Schema({
        vol.Required("method"): cv.string, vol.Optional("data"): vol.Any(dict, list, str, int, float, bool)}))
    hass.services.async_register(DOMAIN, "return_home", return_home, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, "goto", goto, schema=vol.All(vol.Schema({
        vol.Inclusive("x", "robot"): vol.Coerce(int), vol.Inclusive("y", "robot"): vol.Coerce(int),
        vol.Inclusive("pixel_x", "pixel"): vol.Coerce(float), vol.Inclusive("pixel_y", "pixel"): vol.Coerce(float)}),
        cv.has_at_least_one_key("x", "pixel_x")))
