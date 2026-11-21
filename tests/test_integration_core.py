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


def _hdr(w, h, clen, total, origin=(-300, -100)):
    return bytes([0, 0x49, 0, 1]) + w.to_bytes(2, "big") + h.to_bytes(2, "big") + origin[0].to_bytes(2, "big", signed=True) \
        + origin[1].to_bytes(2, "big", signed=True) + b"\x00\x1f\xff\xb3\x00\x00" + clen.to_bytes(2, "big") + total.to_bytes(4, "big")


def test_uncompressed_variant_and_type_byte():
    px = bytes([4] * 12)
    lay = decode_any(_hdr(4, 3, 0, 12) + px)
    assert lay.fmt == "tuya0-raw" and lay.pixels == px and lay.header["type"] == 1


def test_errors_carry_header_not_secrets():
    import pytest
    from lib.map_fetch import MapFetchError
    with pytest.raises(MapFetchError, match="truncated"):
        decode_any(_hdr(4, 3, 50, 12) + b"\x00" * 5)
    with pytest.raises(MapFetchError, match="too short"):
        decode_any(b"\x00" * 10)


def test_at_dock():
    st = MapState(); st.update_layout(layout(), 0)
    st.update_frames(5, [pose(9, -40)], 1.0)          # ~4 px from dock, first poll = not active
    assert st.at_dock(1.0)
    st.update_frames(5, [pose(-150, 50)], 2.0)
    assert not st.at_dock(3.0)                       # moving and away from dock


def test_room_names_and_app_orientation():
    from lib.engine import parse_room_names
    assert parse_room_names("1=Living Room, 2=Hallway; x=bad, 3=") == {1: "Living Room", 2: "Hallway"}
    st = MapState(); st.update_layout(layout(w=40, h=30), 0)
    stored, app = render_png(st, scale=1), render_png(st, scale=1, orientation="app")
    size = lambda png: (int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big"))
    assert size(stored) == (40, 30) and size(app) == (30, 40)


def test_docked_robot_drawn_on_dock():
    from lib.engine import MapState
    from types import SimpleNamespace
    st = MapState()
    st.layout = SimpleNamespace(header={"origin": (-2330, -650)}, width=287, height=171, pixels=b"")
    st.poses = [(-1000, -100)]
    assert st.robot != st.dock
    v = st.version
    st.set_docked(True)
    assert st.robot == st.dock == (233.0, 65.0) and st.version == v + 1
    st.set_docked(False)
    assert st.robot == st.to_pixel((-1000, -100))


def test_render_crops_to_rooms():
    import struct
    from types import SimpleNamespace
    from lib.engine import MapState
    from lib.render import content_box, render_png
    W, H = 100, 80
    px = bytearray([0xFF]) * (W * H)
    for y in range(30, 50):
        for x in range(40, 70):
            px[y * W + x] = 2 << 2
    px[1 * W + 1] = 0xFC  # stray speck far away is ignored
    st = MapState()
    st.layout = SimpleNamespace(width=W, height=H, pixels=bytes(px), header={"origin": (-500, -400)})  # dock at (50, 40)
    assert content_box(st) == (34, 24, 76, 56)
    png = render_png(st, 3, None, "stored")
    assert struct.unpack(">II", png[16:24]) == (42 * 3, 32 * 3)


def test_calibration_matches_render_dot():
    from types import SimpleNamespace
    from lib.engine import MapState
    from lib.render import calibration_points, content_box
    W, H = 100, 80
    px = bytearray([0xFF]) * (W * H)
    for y in range(30, 50):
        for x in range(40, 70):
            px[y * W + x] = 2 << 2
    st = MapState()
    st.layout = SimpleNamespace(width=W, height=H, pixels=bytes(px), header={"origin": (-500, -400)})
    pts = calibration_points(st, 3, "app")
    box = content_box(st)
    dock_img = ((40 - box[1]) * 3 + 1.5, (50 - box[0]) * 3 + 1.5)  # transposed
    assert (pts[0]["map"]["x"], pts[0]["map"]["y"]) == dock_img


def test_room_shapes_cover_room():
    from types import SimpleNamespace
    from lib.engine import MapState
    from lib.render import room_shapes
    W, H = 100, 80
    px = bytearray([0xFF]) * (W * H)
    for y in range(30, 50):
        for x in range(40, 70):
            px[y * W + x] = 2 << 2
    st = MapState()
    st.layout = SimpleNamespace(width=W, height=H, pixels=bytes(px), header={"origin": (-500, -400)})
    shape = room_shapes(st)[2]
    xs = [p[0] for p in shape["outline"]]
    ys = [p[1] for p in shape["outline"]]
    assert min(xs) == 40 * 10 - 500 and max(xs) == 69 * 10 - 500
    assert min(ys) == 30 * 10 - 400 and max(ys) == 49 * 10 - 400
    cx, cy = shape["centre"]
    assert 40 * 10 - 500 < cx < 70 * 10 - 500 and 30 * 10 - 400 < cy < 50 * 10 - 400


def _inside(p, poly):
    x, y = p
    c = False
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def test_adjacent_l_shaped_rooms_do_not_overlap():
    from types import SimpleNamespace
    from lib.engine import MapState
    from lib.render import room_shapes
    W, H = 60, 50
    px = bytearray([0xFF]) * (W * H)
    for y in range(5, 45):           # room 4: an L
        for x in range(5, 25):
            px[y * W + x] = 4 << 2
    for y in range(30, 45):
        for x in range(25, 50):
            px[y * W + x] = 4 << 2
    for y in range(5, 29):           # room 6 tucked into the L's corner, touching it
        for x in range(26, 50):
            px[y * W + x] = 6 << 2
    st = MapState()
    st.layout = SimpleNamespace(width=W, height=H, pixels=bytes(px), header={"origin": (0, 0)})
    shapes = room_shapes(st)
    for room in (4, 6):
        poly = shapes[room]["outline"]
        assert _inside(shapes[room]["centre"], poly)
        other = shapes[10 - room]["outline"]
        assert not _inside(shapes[room]["centre"], other)
    # sample every room pixel: a tap there lands in its own room only
    wrong = 0
    for y in range(H):
        for x in range(W):
            r = px[y * W + x] >> 2
            if r in (4, 6):
                hits = [k for k in (4, 6) if _inside((x * 10 + 0.1, y * 10 + 0.1), shapes[k]["outline"])]
                if hits and hits != [r]:
                    wrong += 1
    assert wrong == 0


def test_landscape_calibration_matches_dock():
    from types import SimpleNamespace
    from lib.engine import MapState
    from lib.render import calibration_points, content_box
    W, H = 100, 80
    px = bytearray([0xFF]) * (W * H)
    for y in range(30, 50):
        for x in range(40, 70):
            px[y * W + x] = 2 << 2
    st = MapState()
    st.layout = SimpleNamespace(width=W, height=H, pixels=bytes(px), header={"origin": (-500, -400)})
    x0, y0, x1, y1 = content_box(st)
    sx, sy = (50 - x0) * 3 + 1.5, (40 - y0) * 3 + 1.5
    r = calibration_points(st, 3, "landscape_right")[0]["map"]
    left = calibration_points(st, 3, "landscape_left")[0]["map"]
    assert (r["x"], r["y"]) == ((x1 - x0) * 3 - sx, sy)
    assert (left["x"], left["y"]) == (sx, (y1 - y0) * 3 - sy)


def test_compact_padding_shifts_calibration():
    from types import SimpleNamespace
    from lib.engine import MapState
    from lib.render import calibration_points, pad_left
    assert pad_left(90, 120, 4 / 3) == 35 and pad_left(200, 100, 4 / 3) == 0
    W, H = 100, 80
    px = bytearray([0xFF]) * (W * H)
    for y in range(30, 70):
        for x in range(45, 60):
            px[y * W + x] = 2 << 2
    st = MapState()
    st.layout = SimpleNamespace(width=W, height=H, pixels=bytes(px), header={"origin": (-500, -400)})
    full = calibration_points(st, 3, "stored")[0]["map"]
    compact = calibration_points(st, 3, "stored", True, 4 / 3)[0]["map"]
    assert compact["x"] - full["x"] == 63 and compact["y"] == full["y"]
