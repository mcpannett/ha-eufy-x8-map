"""Home Assistant-free core for the Eufy X8 map (ha-eufy-x8-map, Apache-2.0).

- account_login(): one-off Eufy login. Returns the vacuums plus the Tuya account
  details. The password is used here only and is never returned or stored.
- X8Client: talks to the cloud with the stored account details (no password).
- MapState: holds the latest layout and the route of the current clean, and
  works out activity, robot position and current room.

Coordinates (X8 T2262A, verified 20 Nov 2026): a pose (x, y) from
``tuya.m.device.media.latest`` maps to layout pixel ((x - origin_x) / 10,
(y - origin_y) / 10), where origin comes from the layout header. Pose (0, 0)
is the dock. Raw poses are stored so the route stays right when the map grows
and its origin moves.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .countries import (
    get_phone_code_by_country_code, get_phone_code_by_region,
    get_region_by_country_code, get_region_by_phone_code,
)
from .eufywebapi import EufyLogon
from .map_fetch import ROOM_ID_MAX, pose_offset
from .tuyawebapi import TuyaAPIError, TuyaAPISession
from .commands import HISTORY_ACTIONS, pick_dps
from .x8map import AnyLayout, Storage, decode_any


class LoginError(Exception):
    """Eufy refused the login or could not be reached. Message is safe to show."""


@dataclass(frozen=True)
class Account:
    """What the integration stores: no password."""

    user_id: str
    region: str
    timezone: str
    phone_code: str

    def as_dict(self) -> dict[str, str]:
        return {"user_id": self.user_id, "region": self.region, "timezone": self.timezone, "phone_code": self.phone_code}


def account_login(email: str, password: str) -> tuple[Account, list[dict[str, str]]]:
    """Log in once; return the account details and the vacuums on it."""
    eufy = EufyLogon(email, password)
    resp = eufy.get_user_info()
    if resp is None or resp.status_code != 200:
        raise LoginError("cannot_connect")
    user = resp.json()
    if user.get("res_code") != 1:
        raise LoginError("invalid_auth")
    info = user["user_info"]
    devices_resp = eufy.get_device_info(info["request_host"], info["id"], user["access_token"])
    settings_resp = eufy.get_user_settings(info["request_host"], info["id"], user["access_token"])
    if devices_resp is None or settings_resp is None:
        raise LoginError("cannot_connect")
    tuya_home = settings_resp.json().get("setting", {}).get("home_setting", {}).get("tuya_home", {})
    if "tuya_region_code" in tuya_home:
        region = tuya_home["tuya_region_code"]
        phone = info.get("phone_code") or get_phone_code_by_region(region)
    elif info.get("phone_code"):
        region, phone = get_region_by_phone_code(info["phone_code"]), info["phone_code"]
    elif info.get("country"):
        region, phone = get_region_by_country_code(info["country"]), get_phone_code_by_country_code(info["country"])
    else:
        region, phone = "EU", "44"
    vacuums = [
        {"id": d["id"], "name": d.get("alias_name") or d.get("name") or "RoboVac",
         "model": d.get("product", {}).get("product_code", "")}
        for d in devices_resp.json().get("devices", [])
        if d.get("product", {}).get("appliance") == "Cleaning"
    ]
    return Account(info["id"], region, info.get("timezone") or "Europe/London", str(phone)), vacuums


class X8Client:
    """Cloud reads for one vacuum. Blocking: call from an executor."""

    def __init__(self, account: Account, dev_id: str) -> None:
        self._account = account
        self.dev_id = dev_id
        self._session: TuyaAPISession | None = None

    def _tuya(self) -> TuyaAPISession:
        if self._session is None:
            a = self._account
            self._session = TuyaAPISession(username="eh-" + a.user_id, region=a.region,
                                           timezone=a.timezone, phone_code=a.phone_code)
        return self._session

    def _retry(self, fn: Any) -> Any:
        """Run fn(session); on a Tuya error, start a fresh session and try once more."""
        try:
            return fn(self._tuya())
        except TuyaAPIError:
            self._session = None
            return fn(self._tuya())

    def fetch_layout(self) -> AnyLayout:
        return decode_any(self.fetch_layout_raw())

    def fetch_layout_raw(self) -> bytes:
        """The robot's saved room layout file (kept by HA as an offline copy)."""
        def run(session: TuyaAPISession) -> bytes:
            storage = Storage(session, self.dev_id)
            code, body = storage.fetch(f"{storage.root}/layout/lay.bin")
            if code != 200 or not body:
                raise ConnectionError(f"map download failed (HTTP {code})")
            decode_any(body)  # validate before anyone caches it
            return body
        return self._retry(run)

    def fetch_frames(self) -> tuple[int, list[str]]:
        def run(session: TuyaAPISession) -> tuple[int, list[str]]:
            resp = session._request(action="tuya.m.device.media.latest", version="3.0",  # noqa: SLF001
                                    data={"devId": self.dev_id, "start": 0, "size": 20})
            return int(resp.get("subRecordId") or 0), list(resp.get("dataList") or [])
        return self._retry(run)

    def publish_dps(self, dps: dict[str, Any]) -> Any:
        """Send DPS to the robot through the cloud (doesn't need the robot's single local connection)."""
        import json as _json
        return self._retry(lambda session: session._request(  # noqa: SLF001
            action="tuya.m.device.dp.publish", version="1.0",
            data={"devId": self.dev_id, "gwId": self.dev_id, "dps": _json.dumps(dps, separators=(",", ":"))}))

    def local_key(self) -> bytes:
        """The robot's LAN key, for in-memory use by the local link only. Never log, store or return it to users."""
        key = self._retry(lambda session: session._request(  # noqa: SLF001
            action="tuya.m.device.get", version="1.0", data={"devId": self.dev_id})).get("localKey") or ""
        if not key:
            raise ValueError("no local key on the account")
        return key.encode()[:16]

    def decode_flows(self, flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Decrypt captured app<->robot traffic. The local key is fetched and used here only; never returned."""
        from .capture import decode_flows
        return decode_flows(flows, self.local_key().decode())

    def fetch_dps(self) -> dict[str, Any]:
        """Current state DPS (15, 122, 124, 125, 142 only; never the local key)."""
        return self._retry(lambda session: pick_dps(session._request(  # noqa: SLF001
            action="tuya.m.device.get", version="1.0", data={"devId": self.dev_id})))

    def fetch_dp_history(self, start_ms: int, end_ms: int, dp_ids: str = "124,125") -> tuple[str | None, Any, list[str]]:
        """Try the cloud DP-log calls. Returns (action used, raw result, errors by action)."""
        errors: list[str] = []
        for action, version in HISTORY_ACTIONS:
            def run(session: TuyaAPISession, action: str = action, version: str = version) -> Any:
                return session._request(action=action, version=version, data={  # noqa: SLF001
                    "devId": self.dev_id, "dpIds": dp_ids, "offset": 0, "limit": 50,
                    "startTime": str(start_ms), "endTime": str(end_ms), "sortType": "DESC"})
            try:
                return action, run(self._tuya()), errors  # no session reset: an unknown call is not a session problem
            except TuyaAPIError as err:
                errors.append(f"{action}: {str(err).split(':')[0]}")
            except Exception as err:  # noqa: BLE001
                errors.append(f"{action}: {type(err).__name__}")
        return None, None, errors


MAX_ROUTE_POINTS = 20_000
ACTIVE_WINDOW_S = 90.0


@dataclass
class MapState:
    """Latest map and the current clean's route. Pure data + logic."""

    layout: AnyLayout | None = None
    layout_time: float | None = None
    record_id: int = 0
    poses: list[tuple[int, int]] = field(default_factory=list)  # raw, deduplicated
    last_frames: tuple[str, ...] = ()
    last_change: float | None = None  # monotonic time the frames last changed
    version: int = 0  # bumps whenever something drawable changes
    docked: bool = False  # robot reports it is on the dock: draw it there (cloud frames stop before it arrives)

    def set_docked(self, docked: bool) -> None:
        if docked != self.docked:
            self.docked = docked
            self.version += 1

    def update_layout(self, layout: AnyLayout, now: float) -> None:
        if self.layout is None or layout.pixels != self.layout.pixels or layout.header != self.layout.header:
            self.version += 1
        self.layout, self.layout_time = layout, now

    def update_frames(self, record_id: int, frames: list[str], now: float) -> bool:
        """Add a poll result. Returns True if the route changed."""
        if not record_id or not frames:
            return False
        key = tuple(frames)
        if key != self.last_frames:
            if self.last_frames:  # the first poll after start-up is not "activity"
                self.last_change = now
            self.last_frames = key
        if record_id != self.record_id:
            self.record_id, self.poses = record_id, []
        changed = False
        for frame in frames:
            pose = pose_offset(frame)
            if pose is not None and (not self.poses or self.poses[-1] != pose):
                self.poses.append(pose)
                changed = True
        if changed:
            del self.poses[:-MAX_ROUTE_POINTS]
            self.version += 1
        return changed

    def is_active(self, now: float) -> bool:
        return self.last_change is not None and now - self.last_change < ACTIVE_WINDOW_S

    def to_pixel(self, pose: tuple[int, int]) -> tuple[float, float] | None:
        if not self.layout or "origin" not in self.layout.header:
            return None
        ox, oy = self.layout.header["origin"]
        return (pose[0] - ox) / 10, (pose[1] - oy) / 10

    def to_pose(self, pixel: tuple[float, float]) -> tuple[int, int] | None:
        """Inverse of to_pixel (stored orientation)."""
        if not self.layout or "origin" not in self.layout.header:
            return None
        ox, oy = self.layout.header["origin"]
        return round(pixel[0] * 10 + ox), round(pixel[1] * 10 + oy)

    @property
    def robot(self) -> tuple[float, float] | None:
        if self.docked and self.dock is not None:
            return self.dock
        return self.to_pixel(self.poses[-1]) if self.poses else None

    @property
    def dock(self) -> tuple[float, float] | None:
        return self.to_pixel((0, 0))

    def at_dock(self, now: float, radius_px: float = 6.0) -> bool:
        """Robot sits within radius_px of the dock and nothing is moving."""
        r, d = self.robot, self.dock
        return bool(r and d and not self.is_active(now) and math.hypot(r[0] - d[0], r[1] - d[1]) <= radius_px)

    def room_at(self, point: tuple[float, float] | None) -> int | None:
        if not point or not self.layout:
            return None
        x, y = int(point[0]), int(point[1])
        if not (0 <= x < self.layout.width and 0 <= y < self.layout.height):
            return None
        room = self.layout.pixels[y * self.layout.width + x] >> 2
        return room if room <= ROOM_ID_MAX else None

    @property
    def rooms(self) -> list[int]:
        if not self.layout:
            return []
        return sorted({b >> 2 for b in self.layout.pixels if (b >> 2) <= ROOM_ID_MAX})

    def route_pixels(self, max_jump: float = 60.0) -> list[list[tuple[float, float]]]:
        """Route in pixels, split where it leaves the map or jumps implausibly."""
        if not self.layout:
            return []
        pieces: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        w, h = self.layout.width, self.layout.height
        for pose in self.poses:
            p = self.to_pixel(pose)
            if p is None or not (-3 <= p[0] < w + 3 and -3 <= p[1] < h + 3):
                if current:
                    pieces.append(current)
                current = []
                continue
            if current and math.hypot(p[0] - current[-1][0], p[1] - current[-1][1]) > max_jump:
                pieces.append(current)
                current = []
            current.append(p)
        if current:
            pieces.append(current)
        return pieces


def parse_room_names(text: str | None) -> dict[int, str]:
    """'1=Living Room, 2=Hallway' -> {1: 'Living Room', 2: 'Hallway'}. Bad parts are ignored."""
    names: dict[int, str] = {}
    for part in (text or "").replace(";", ",").replace("\n", ",").split(","):
        if "=" in part:
            key, _, name = part.partition("=")
            if key.strip().isdigit() and name.strip():
                names[int(key.strip())] = name.strip()[:40]
    return names


def format_room_names(names: dict[int, str]) -> str:
    """{1: 'Porch', 2: 'Hallway'} -> '1=Porch, 2=Hallway' (the options text, sorted by room)."""
    return ", ".join(f"{room}={names[room]}" for room in sorted(names))


def rename_room(text: str | None, room: int, name: str) -> str:
    """Return the options text with one room renamed. Empty name = back to the default 'Room N'."""
    clean = " ".join(name.replace(",", " ").replace(";", " ").replace("=", " ").split())[:40]
    names = parse_room_names(text)
    if clean:
        names[room] = clean
    else:
        names.pop(room, None)
    return format_room_names(names)
