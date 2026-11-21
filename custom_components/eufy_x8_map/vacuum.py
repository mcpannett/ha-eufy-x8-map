"""Vacuum entity for Eufy X8 Map: local control plus HA area cleaning (rooms as segments)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import StateVacuumEntity, VacuumActivity, VacuumEntityFeature

try:  # area cleaning (segments) arrived in newer Home Assistant releases
    from homeassistant.components.vacuum import Segment
    AREA = VacuumEntityFeature.CLEAN_AREA
except ImportError:  # older HA: everything else still works
    Segment = None  # type: ignore[assignment]
    AREA = VacuumEntityFeature(0)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ROOM_NAMES, DOMAIN, SUCTION_LEVELS
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity
from .lib.engine import parse_room_names

ACTIVITY = {
    "Charging": VacuumActivity.DOCKED,
    "Sleeping": VacuumActivity.DOCKED,
    "Running": VacuumActivity.CLEANING,
    "Locating": VacuumActivity.CLEANING,
    "Recharge": VacuumActivity.RETURNING,
    "standby": VacuumActivity.IDLE,
    "completed": VacuumActivity.IDLE,
    "recharge_needed": VacuumActivity.ERROR,
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([X8Vacuum(hass.data[DOMAIN][entry.entry_id])])


class X8Vacuum(X8MapEntity, StateVacuumEntity):
    """The X8 itself. Rooms are offered to HA as segments for area cleaning (two passes)."""

    _attr_fan_speed_list = SUCTION_LEVELS
    _attr_supported_features = (
        VacuumEntityFeature.STATE | VacuumEntityFeature.START | VacuumEntityFeature.PAUSE | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME | VacuumEntityFeature.FAN_SPEED | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.CLEAN_SPOT | AREA
    )

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        super().__init__(coordinator, "vacuum")
        self._attr_translation_key = None
        self._attr_name = None  # use the device name

    @property
    def activity(self) -> VacuumActivity | None:
        status = self.coordinator.dp("15")
        error = self.coordinator.dp("106")
        if error not in (None, 0, "0", "no_error"):
            return VacuumActivity.ERROR
        if status == "standby" and self.coordinator.dp("2") is False and self.coordinator.tracker.running:
            return VacuumActivity.PAUSED
        if self.coordinator.is_docked:
            return VacuumActivity.DOCKED
        return ACTIVITY.get(status)

    @property
    def fan_speed(self) -> str | None:
        value = self.coordinator.dp("102")
        return value if value in SUCTION_LEVELS else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"battery": self.coordinator.dp("104"), "status": self.coordinator.dp("15"),
                "last_run": self.coordinator.last_run or None}

    async def _do(self, coro) -> None:
        try:
            await coro
        except PermissionError as err:
            raise ServiceValidationError(str(err)) from None
        except ValueError as err:
            raise ServiceValidationError(str(err)) from None
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Send failed ({type(err).__name__})") from None

    async def async_start(self) -> None:
        action = "resume" if self.activity == VacuumActivity.PAUSED else "start"
        await self._do(self.coordinator.async_vacuum(action))

    async def async_pause(self) -> None:
        await self._do(self.coordinator.async_vacuum("pause"))

    async def async_stop(self, **kwargs: Any) -> None:
        await self._do(self.coordinator.async_vacuum("pause"))

    async def async_return_to_base(self, **kwargs: Any) -> None:
        if self.coordinator.is_docked:
            raise ServiceValidationError("The robot is already docked")
        await self._do(self.coordinator.async_vacuum("home"))

    async def async_locate(self, **kwargs: Any) -> None:
        await self._do(self.coordinator.async_vacuum("locate"))

    async def async_clean_spot(self, **kwargs: Any) -> None:
        await self._do(self.coordinator.async_vacuum("spot_here"))

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        await self._do(self.coordinator.async_set_suction(fan_speed))

    async def async_get_segments(self) -> list:
        names = parse_room_names(self.coordinator.entry.options.get(CONF_ROOM_NAMES))
        return [Segment(id=str(r), name=names.get(r, f"Room {r}")) for r in self.coordinator.state.rooms]

    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        rooms = [int(s) for s in segment_ids if str(s).isdigit() and int(s) in self.coordinator.state.rooms]
        if not rooms:
            raise ServiceValidationError("None of those rooms are on the current map")
        await self._do(self.coordinator.async_clean_rooms(rooms))
