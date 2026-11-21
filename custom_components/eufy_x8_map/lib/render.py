"""Draw a MapState as a PNG with the standard library only (ha-eufy-x8-map, Apache-2.0)."""
from __future__ import annotations

import struct
import zlib

from .engine import MapState

BACKGROUND = (0, 0, 0, 0)          # transparent outside the house
WALL = (176, 176, 184, 255)       # light grey: visible on dark and light dashboards
OTHER = (150, 150, 150, 255)       # 0xFC: carpet/edge/unknown marks
ROUTE = (255, 255, 255, 230)
ROBOT = (25, 118, 210, 255)
DOCK = (211, 47, 47, 255)
ROOM_COLOURS = [(129, 199, 132), (77, 182, 172), (255, 183, 77), (121, 134, 203), (240, 98, 146),
                (174, 213, 129), (100, 181, 246), (255, 138, 101), (149, 117, 205), (220, 231, 117)]


def _png(width: int, height: int, rgba: bytearray) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    stride = width * 4
    rows = b"".join(b"\x00" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def content_box(state: MapState, margin: int = 6) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) in layout pixels around rooms, walls, dock, robot and route; x1/y1 exclusive."""
    lay = state.layout
    if lay is None:
        return None
    xs: list[int] = []
    ys: list[int] = []
    for y in range(lay.height):
        row = lay.pixels[y * lay.width:(y + 1) * lay.width]
        # Rooms only: stray wall/obstacle specks far from the house would otherwise keep the empty space.
        idx = [x for x, b in enumerate(row) if (b >> 2) <= 31]
        if idx:
            xs += (idx[0], idx[-1])
            ys.append(y)
    points = [state.dock] if state.dock else []  # rooms + dock only, so the crop (and calibration) stays stable
    for px, py in points:
        xs.append(int(px))
        ys.append(int(py))
    if not xs:
        return None
    return (max(0, min(xs) - margin), max(0, min(ys) - margin),
            min(lay.width, max(xs) + margin + 1), min(lay.height, max(ys) + margin + 1))


def render_png(state: MapState, scale: int = 3, room_colours: dict[int, tuple[int, int, int]] | None = None,
               orientation: str = "stored", crop: bool = True) -> bytes | None:
    """Return a PNG of the map with route, dock and robot, or None without a map."""
    lay = state.layout
    if lay is None:
        return None
    w, h = lay.width * scale, lay.height * scale
    rgba = bytearray(w * h * 4)
    for y in range(lay.height):
        row = lay.pixels[y * lay.width:(y + 1) * lay.width]
        line = bytearray()
        for byte in row:
            room = byte >> 2
            if room <= 31:
                colour = (*((room_colours or {}).get(room) or ROOM_COLOURS[room % len(ROOM_COLOURS)]), 255)
            elif byte == 0xFD or room == 62:
                colour = WALL
            elif byte == 0xFC:
                colour = OTHER
            else:
                colour = BACKGROUND
            line += bytes(colour) * scale
        for dy in range(scale):
            start = ((y * scale + dy) * w) * 4
            rgba[start:start + len(line)] = line

    def dot(px: float, py: float, colour: tuple[int, int, int, int], r: int) -> None:
        cx, cy = int(px * scale + scale / 2), int(py * scale + scale / 2)
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                if 0 <= xx < w and 0 <= yy < h and (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r + r:
                    i = (yy * w + xx) * 4
                    rgba[i:i + 4] = bytes(colour)

    for piece in state.route_pixels():
        for i in range(1, len(piece)):
            (x0, y0), (x1, y1) = piece[i - 1], piece[i]
            steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) * scale))
            for s in range(steps + 1):
                dot(x0 + (x1 - x0) * s / steps, y0 + (y1 - y0) * s / steps, ROUTE, 0)
    if state.dock:
        dot(*state.dock, DOCK, 2 * scale)
    if state.robot:
        dot(*state.robot, ROBOT, 2 * scale)
    if crop:  # trim empty space around the house so the card isn't mostly blank
        box = content_box(state)
        if box:
            x0, y0, x1, y1 = (v * scale for v in box)
            cw, ch = x1 - x0, y1 - y0
            cropped = bytearray(cw * ch * 4)
            for yy in range(ch):
                src = ((y0 + yy) * w + x0) * 4
                cropped[yy * cw * 4:(yy + 1) * cw * 4] = rgba[src:src + cw * 4]
            rgba, w, h = cropped, cw, ch
    if orientation == "app":  # the Eufy app shows the map transposed (x and y swapped)
        out = bytearray(len(rgba))
        for y in range(h):
            row = rgba[y * w * 4:(y + 1) * w * 4]
            for x in range(w):
                j = (x * h + y) * 4
                out[j:j + 4] = row[x * 4:x * 4 + 4]
        return _png(h, w, out)
    return _png(w, h, rgba)


def calibration_points(state: MapState, scale: int = 3, orientation: str = "stored", crop: bool = True) -> list[dict] | None:
    """Three robot-pose <-> image-pixel pairs for map cards (e.g. the Xiaomi Vacuum Map card)."""
    lay = state.layout
    if lay is None or "origin" not in getattr(lay, "header", {}):
        return None
    x0, y0 = (0, 0)
    if crop:
        box = content_box(state)
        if box:
            x0, y0 = box[0], box[1]
    points = []
    for pose in ((0, 0), (1000, 0), (0, 1000)):
        px, py = state.to_pixel(pose)
        ix, iy = (px - x0) * scale + scale / 2, (py - y0) * scale + scale / 2
        if orientation == "app":
            ix, iy = iy, ix
        points.append({"vacuum": {"x": pose[0], "y": pose[1]}, "map": {"x": round(ix, 1), "y": round(iy, 1)}})
    return points


def room_shapes(state: MapState, band: int = 3) -> dict[int, dict]:
    """Per room: an outline polygon and a label point, in robot pose units (for map cards).

    The outline follows the room's left and right edges in horizontal bands of `band` pixels,
    which is close enough for tapping a room on the map.
    """
    lay = state.layout
    if lay is None or "origin" not in getattr(lay, "header", {}):
        return {}
    spans: dict[int, list[tuple[int, int, int]]] = {}
    for y0 in range(0, lay.height, band):
        found: dict[int, list[int]] = {}
        for y in range(y0, min(y0 + band, lay.height)):
            row = lay.pixels[y * lay.width:(y + 1) * lay.width]
            for x, b in enumerate(row):
                room = b >> 2
                if room <= 31:
                    f = found.setdefault(room, [x, x])
                    f[0], f[1] = min(f[0], x), max(f[1], x)
        for room, (lo, hi) in found.items():
            spans.setdefault(room, []).append((y0, lo, hi))
    shapes: dict[int, dict] = {}
    for room, rows in spans.items():
        if len(rows) < 2:
            continue
        left = [(lo, y) for y, lo, _ in rows] + [(rows[-1][1], rows[-1][0] + band)]
        right = [(hi + 1, y + band) for y, _, hi in reversed(rows)] + [(rows[0][2] + 1, rows[0][0])]
        poly = [state.to_pose((x, y)) for x, y in left + right]
        mid_y, lo, hi = rows[len(rows) // 2]
        shapes[room] = {"outline": [list(p) for p in poly], "centre": list(state.to_pose(((lo + hi) / 2, mid_y + band / 2)))}
    return shapes
