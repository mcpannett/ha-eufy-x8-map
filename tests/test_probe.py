import sys, struct
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "probe"))
from pathlib import Path
import probe
from x8probe.map_fetch import decode_layout, RouteTracker

def lz4_literals(data: bytes) -> bytes:
    # one literal-only sequence (valid final block)
    n = len(data); out = bytearray()
    if n >= 15:
        out.append(0xF0); rest = n - 15
        while rest >= 255: out.append(255); rest -= 255
        out.append(rest)
    else: out.append(n << 4)
    return bytes(out) + data


def test_decode_route_and_png(tmp_path):
    W, H = 40, 30
    px = bytearray()
    for y in range(H):
        for x in range(W):
            room = 0 if x < 20 else 1
            if y in (0, H-1) or x in (0, W-1): room = 62
            px.append(room << 2 | 1)
    dock = (100, 150)  # 1/10 px -> (10,15)
    hdr = b"0pam" + b"\0"*4 + W.to_bytes(2,"big") + H.to_bytes(2,"big") + dock[0].to_bytes(2,"big") + dock[1].to_bytes(2,"big") + b"\0"*8
    raw = hdr + lz4_literals(bytes(px))
    lay = decode_layout(raw)
    assert (lay.width, lay.height, lay.dock) == (W, H, dock)
    assert {r["id"] for r in lay.room_summary()} == {0, 1}

    def zz(v): return (v << 1) ^ (v >> 63)
    def varint(v):
        out = bytearray()
        while True:
            b = v & 0x7F; v >>= 7
            out.append(b | (0x80 if v else 0))
            if not v: return bytes(out)
    def pose(dx, dy):
        body = b"\x08" + varint(zz(dx)) + b"\x10" + varint(zz(dy)) + b"\x18\x00"
        return (bytes([len(body)]) + body).hex()

    t = RouteTracker(); t.set_layout_dock(dock)
    assert t.update(7, [pose(0,0), pose(50,0), pose(100,20)])
    pts = t.path(); assert pts[-1] == (20.0, 17.0), pts
    probe.OUT = tmp_path
    probe.write_png(probe.OUT/"map.png", *probe.render(lay, t.segments(W, H)))
    assert (probe.OUT/"map.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    pass

