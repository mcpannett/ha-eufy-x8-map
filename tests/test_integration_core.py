"""Tests for the HA-free core of the integration (engine + render), using synthetic data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "eufy_x8_map"))
from lib.engine import MapState, X8Client, Account  # noqa: E402
from lib.render import render_png  # noqa: E402
from lib.x8map import decode_any  # noqa: E402
from lib.tuyawebapi import TuyaAPIError  # noqa: E402


def lz4_literals(data: bytes) -> bytes:
    n = len(data); out = bytearray()
    if n >= 15:
        out.append(0xF0); rest = n - 15
        while rest >= 255: out.append(255); rest -= 255
        out.append(rest)
    else:
        out.append(n << 4)
    return bytes(out) + data


def layout(w=40, h=30, origin=(-300, -100)):
    px = bytes((((1 if x < 20 else 2) << 2) if 0 < x < w - 1 and 0 < y < h - 1 else 0xFD) for y in range(h) for x in range(w))
    block = lz4_literals(px)
    hdr = bytes([0, 0x49, 0, 0]) + w.to_bytes(2, "big") + h.to_bytes(2, "big") + origin[0].to_bytes(2, "big", signed=True) \
        + origin[1].to_bytes(2, "big", signed=True) + b"\x00\x1f\xff\xb3\x00\x00" + len(block).to_bytes(2, "big") + (w * h).to_bytes(4, "big")
    return decode_any(hdr + block)


def zz(v): return (v << 1) ^ (v >> 63)
def varint(v):
    out = bytearray()
    while True:
        b = v & 0x7F; v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v: return bytes(out)
def pose(x, y):
    body = b"\x08" + varint(zz(x)) + b"\x10" + varint(zz(y)) + b"\x18\x00"
    return (bytes([len(body)]) + body).hex()


def test_pose_to_pixel_room_and_dock():
    st = MapState(); st.update_layout(layout(), 0)
    assert st.dock == (30.0, 10.0)             # pose (0,0) -> (-origin)/10
    st.update_frames(5, [pose(0, 0), pose(-150, 50)], 1.0)
    assert st.robot == (15.0, 15.0) and st.room_at(st.robot) == 1
    st.update_frames(5, [pose(-50, 50)], 2.0)
    assert st.room_at(st.robot) == 2 and len(st.poses) == 3


def test_route_survives_origin_change():
    st = MapState(); st.update_layout(layout(), 0)
    st.update_frames(5, [pose(-150, 50)], 1.0)
    st.update_layout(layout(w=50, h=40, origin=(-400, -200)), 2.0)   # map grew
    assert st.robot == (25.0, 25.0)


def test_new_clean_resets_route_and_activity():
    st = MapState(); st.update_layout(layout(), 0)
    st.update_frames(5, [pose(1, 1)], 1.0)
    assert not st.is_active(1.0)               # first poll after start-up is not activity
    st.update_frames(5, [pose(2, 2)], 2.0)
    assert st.is_active(10.0) and not st.is_active(200.0)
    st.update_frames(6, [pose(3, 3)], 3.0)
    assert st.record_id == 6 and st.poses == [(3, 3)]
    assert not st.update_frames(0, [], 4.0)    # empty poll never resets


def test_stale_identical_frames_are_not_activity():
    st = MapState()
    for t in range(5):
        st.update_frames(73, ["0908b50910da0518920a"], float(t))
    assert not st.is_active(5.0) and len(st.poses) == 1


def test_route_splits_on_jumps():
    st = MapState(); st.update_layout(layout(), 0)
    st.update_frames(5, [pose(-10, 10), pose(-20, 10), pose(-290, 190)], 1.0)
    assert len(st.route_pixels(max_jump=10)) == 2


def test_render_png():
    st = MapState()
    assert render_png(st) is None
    st.update_layout(layout(), 0); st.update_frames(5, [pose(0, 0), pose(-150, 50)], 1.0)
    png = render_png(st, scale=2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(png[16:20], "big") == 80


def test_client_retries_once_with_fresh_session(monkeypatch):
    client = X8Client(Account("u1", "EU", "Europe/London", "44"), "dev")
    sessions = []

    class FakeSession:
        def __init__(self, **kw):
            sessions.append(self); self.calls = 0
        def _request(self, **kw):
            if len(sessions) == 1:
                raise TuyaAPIError("USER_SESSION_INVALID", {})
            return {"subRecordId": 9, "dataList": ["ab"]}
    monkeypatch.setattr("lib.engine.TuyaAPISession", FakeSession)
    assert client.fetch_frames() == (9, ["ab"]) and len(sessions) == 2
