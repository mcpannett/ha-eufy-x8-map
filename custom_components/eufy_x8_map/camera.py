"""Map camera for Eufy X8 Map: floor plan, rooms, route, dock and robot as a PNG."""
from __future__ import annotations

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ORIENTATION, CONF_ROOM_NAMES, DOMAIN
from .coordinator import X8MapCoordinator
from .entity import X8MapEntity
from .lib.engine import parse_room_names
from .lib.render import render_png


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([X8MapCamera(hass.data[DOMAIN][entry.entry_id])])


class X8MapCamera(X8MapEntity, Camera):
    _attr_content_type = "image/png"
    _attr_frame_interval = 2.0

    def __init__(self, coordinator: X8MapCoordinator) -> None:
        X8MapEntity.__init__(self, coordinator, "map")
        Camera.__init__(self)
        self._png: bytes | None = None
        self._png_version: tuple = ()

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        state = self.coordinator.state
        orientation = self.coordinator.entry.options.get(CONF_ORIENTATION, "app")
        key = (state.version, orientation, state.docked)
        if key != self._png_version:
            self._png = await self.hass.async_add_executor_job(render_png, state, 3, None, orientation)
            self._png_version = key
        return self._png

    @property
    def extra_state_attributes(self) -> dict:
        state = self.coordinator.state
        lay = state.layout
        return {
            "rooms": state.rooms,
            "room_names": {r: parse_room_names(self.coordinator.entry.options.get(CONF_ROOM_NAMES)).get(r, f"Room {r}") for r in state.rooms},
            "map_width": lay.width if lay else None,
            "map_height": lay.height if lay else None,
            "route_points": len(state.poses),
            "robot_pixel": state.robot,
            "dock_pixel": state.dock,
            "map_format": lay.fmt if lay else None,
            "map_error": self.coordinator.map_error,
            "map_source": self.coordinator.map_source,
            "cloud_error": self.coordinator.cloud_error,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_update_token()
        super()._handle_coordinator_update()
