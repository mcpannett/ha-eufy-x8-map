"""Config flow for Eufy X8 Map. The password is used once to log in and is never stored."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ACCOUNT, CONF_DEVICE_ID, CONF_DEVICE_NAME, CONF_MODEL, CONF_ORIENTATION, CONF_ROOM_NAMES,
    CONF_ROBOT_HOST, CONF_VACUUM_ENTITY, DOMAIN,
)
from .lib.engine import Account, LoginError, account_login


class EufyX8MapConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._account: Account | None = None
        self._vacuums: list[dict[str, str]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._account, self._vacuums = await self.hass.async_add_executor_job(
                    account_login, user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            except LoginError as err:
                errors["base"] = str(err) if str(err) in ("invalid_auth", "cannot_connect") else "unknown"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                if not self._vacuums:
                    return self.async_abort(reason="no_vacuums")
                if len(self._vacuums) == 1:
                    return await self._create(self._vacuums[0])
                return await self.async_step_pick()
        schema = vol.Schema({vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            chosen = next(v for v in self._vacuums if v["id"] == user_input[CONF_DEVICE_ID])
            return await self._create(chosen)
        options = [selector.SelectOptionDict(value=v["id"], label=f"{v['name']} ({v['model']})") for v in self._vacuums]
        schema = vol.Schema({vol.Required(CONF_DEVICE_ID): selector.SelectSelector(
            selector.SelectSelectorConfig(options=options))})
        return self.async_show_form(step_id="pick", data_schema=schema)

    async def _create(self, vacuum: dict[str, str]) -> ConfigFlowResult:
        await self.async_set_unique_id(vacuum["id"])
        self._abort_if_unique_id_configured()
        assert self._account is not None
        return self.async_create_entry(title=f"{vacuum['name']} map", data={
            CONF_ACCOUNT: self._account.as_dict(), CONF_DEVICE_ID: vacuum["id"],
            CONF_DEVICE_NAME: vacuum["name"], CONF_MODEL: vacuum["model"]})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EufyX8MapOptionsFlow()


class EufyX8MapOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        opts = self.config_entry.options
        if user_input is not None:
            # Keep options this form doesn't show (e.g. the command recorder switch).
            return self.async_create_entry(data={**opts, **user_input})
        vac = opts.get(CONF_VACUUM_ENTITY)
        schema = vol.Schema({
            (vol.Optional(CONF_VACUUM_ENTITY, description={"suggested_value": vac}) if vac else vol.Optional(CONF_VACUUM_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="vacuum")),
            vol.Optional(CONF_ROOM_NAMES, default=opts.get(CONF_ROOM_NAMES, "")): str,
            vol.Optional(CONF_ORIENTATION, default=opts.get(CONF_ORIENTATION, "app")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[
                    selector.SelectOptionDict(value="app", label="Match the Eufy app"),
                    selector.SelectOptionDict(value="landscape_right", label="App view turned sideways (Kitchen on the right)"),
                    selector.SelectOptionDict(value="landscape_left", label="App view turned sideways (Kitchen on the left)"),
                    selector.SelectOptionDict(value="stored", label="As stored by the robot")])),
            vol.Optional(CONF_ROBOT_HOST, default=opts.get(CONF_ROBOT_HOST, "")): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
