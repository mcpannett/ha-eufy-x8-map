"""Draw a MapState as a PNG with the standard library only (ha-eufy-x8-map, Apache-2.0)."""
from __future__ import annotations

import struct
import zlib

from .engine import MapState

BACKGROUND = (0, 0, 0, 0)          # transparent outside the house
WALL = (176, 176, 184, 255)       # light grey: visible on dark and light dashboards
OTHER = (150, 150, 150, 255)       # 0xFC: carpet/edge/unknown marks
ROUTE = (255, 255, 255, 255)       # trail core: white ...
ROUTE_EDGE = (20, 20, 20, 255)     # ... with a dark edge, so it stands out on every room colour
ROBOT = (25, 118, 210, 255)
DOCK = (211, 47, 47, 255)
OUTLINE = (255, 255, 255, 255)
ROOM_COLOURS = [(129, 199, 132), (77, 182, 172), (255, 183, 77), (121, 134, 203), (240, 98, 146),
                (174, 213, 129), (100, 181, 246), (255, 138, 101), (149, 117, 205), (220, 231, 117)]


def _png(width: int, height: int, rgba: bytearray) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    stride = width * 4
    rows = b"".join(b"\x00" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def pad_left(w: int, h: int, min_aspect: float) -> int:
    """Columns added on each side to reach min_aspect (0 = none)."""
    if min_aspect <= 0 or w >= h * min_aspect:
        return 0
    return int(round(h * min_aspect - w)) // 2


def _finish(w: int, h: int, rgba: bytearray, min_aspect: float) -> bytes:
    pad = pad_left(w, h, min_aspect)
    if not pad:
        return _png(w, h, rgba)
    nw = w + 2 * pad
    out = bytearray(nw * h * 4)
    for y in range(h):
        out[(y * nw + pad) * 4:(y * nw + pad + w) * 4] = rgba[y * w * 4:(y + 1) * w * 4]
    return _png(nw, h, out)


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
               orientation: str = "stored", crop: bool = True, min_aspect: float = 0.0) -> bytes | None:
    """Return a PNG of the map with route, dock and robot, or None without a map.

    min_aspect (width / height): pad the sides with transparency so a tall map is shown smaller on cards."""
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

    def ring(px: float, py: float, colour: tuple[int, int, int, int], r_out: int, r_in: int) -> None:
        cx, cy = int(px * scale + scale / 2), int(py * scale + scale / 2)
        for yy in range(cy - r_out, cy + r_out + 1):
            for xx in range(cx - r_out, cx + r_out + 1):
                d = (xx - cx) ** 2 + (yy - cy) ** 2
                if 0 <= xx < w and 0 <= yy < h and r_in * r_in < d <= r_out * r_out + r_out:
                    i = (yy * w + xx) * 4
                    rgba[i:i + 4] = bytes(colour)

    def trail(colour: tuple[int, int, int, int], r: int) -> None:
        for piece in state.route_pixels():
            for i in range(1, len(piece)):
                (x0, y0), (x1, y1) = piece[i - 1], piece[i]
                steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) * scale))
                for s in range(steps + 1):
                    dot(x0 + (x1 - x0) * s / steps, y0 + (y1 - y0) * s / steps, colour, r)

    trail(ROUTE_EDGE, max(1, scale - 1))  # bold trail: dark edge, then a white core
    trail(ROUTE, max(1, scale // 2))
    if state.dock:  # base station: a red ring with a white rim, so the docked robot sits visibly inside it
        ring(*state.dock, OUTLINE, 5 * scale, 4 * scale - 2)
        ring(*state.dock, DOCK, 4 * scale - 1, 3 * scale)
        if not state.docked:
            dot(*state.dock, DOCK, scale)
    if state.robot:  # robot: bigger blue dot with a white outline
        r = 2 * scale + 2 if state.docked else 3 * scale
        dot(*state.robot, OUTLINE, r + 2)
        dot(*state.robot, ROBOT, r)
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
        return _finish(h, w, out, min_aspect)
    if orientation in ("landscape_right", "landscape_left"):
        # The app's view turned on its side (wide, fits a phone better). On the stored grid that is a flip.
        out = bytearray(len(rgba))
        stride = w * 4
        for y in range(h):
            src_y = y if orientation == "landscape_right" else h - 1 - y
            row = rgba[src_y * stride:(src_y + 1) * stride]
            if orientation == "landscape_right":
                row = b"".join(row[x * 4:x * 4 + 4] for x in range(w - 1, -1, -1))
            out[y * stride:(y + 1) * stride] = row
        return _finish(w, h, out, min_aspect)
    return _finish(w, h, rgba, min_aspect)


ORIENTATIONS = ("app", "landscape_right", "landscape_left", "stored")


def calibration_points(state: MapState, scale: int = 3, orientation: str = "stored", crop: bool = True,
                       min_aspect: float = 0.0) -> list[dict] | None:
    """Three robot-pose <-> image-pixel pairs for map cards (e.g. the Xiaomi Vacuum Map card)."""
    lay = state.layout
    if lay is None or "origin" not in getattr(lay, "header", {}):
        return None
    x0, y0, x1, y1 = 0, 0, lay.width, lay.height
    if crop:
        box = content_box(state)
        if box:
            x0, y0, x1, y1 = box
    w, h = (x1 - x0) * scale, (y1 - y0) * scale
    out_w, out_h = (h, w) if orientation == "app" else (w, h)
    pad = pad_left(out_w, out_h, min_aspect)
    points = []
    for pose in ((0, 0), (1000, 0), (0, 1000)):
        px, py = state.to_pixel(pose)
        ix, iy = (px - x0) * scale + scale / 2, (py - y0) * scale + scale / 2
        if orientation == "app":
            ix, iy = iy, ix
        elif orientation == "landscape_right":
            ix = w - ix
        elif orientation == "landscape_left":
            iy = h - iy
        ix += pad
        points.append({"vacuum": {"x": pose[0], "y": pose[1]}, "map": {"x": round(ix, 1), "y": round(iy, 1)}})
    return points


def _trace(cells: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Outer boundary of a 4-connected pixel blob (Moore-neighbour tracing), clockwise, pixel centres."""
    start = min(cells, key=lambda c: (c[1], c[0]))
    dirs = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
    path = [start]
    cur, back = start, 4  # we "came from" the left
    for _ in range(len(cells) * 8 + 8):
        for k in range(8):
            d = (back + 1 + k) % 8
            nxt = (cur[0] + dirs[d][0], cur[1] + dirs[d][1])
            if nxt in cells:
                back = (d + 4) % 8
                cur = nxt
                break
        else:
            return path  # single pixel
        if cur == start:
            break
        path.append(cur)
    return path


def _simplify(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker on a closed loop."""
    if len(points) < 4:
        return points

    def rdp(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        dx, dy = x1 - x0, y1 - y0
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        best, idx = 0.0, 0
        for i in range(1, len(pts) - 1):
            d = abs(dy * (pts[i][0] - x0) - dx * (pts[i][1] - y0)) / norm
            if d > best:
                best, idx = d, i
        if best <= eps:
            return [pts[0], pts[-1]]
        return rdp(pts[:idx + 1])[:-1] + rdp(pts[idx:])

    far = max(range(len(points)), key=lambda i: (points[i][0] - points[0][0]) ** 2 + (points[i][1] - points[0][1]) ** 2)
    a = rdp(points[:far + 1])
    b = rdp(points[far:] + [points[0]])
    return a[:-1] + b[:-1]


def room_shapes(state: MapState, eps: float = 1.0) -> dict[int, dict]:
    """Per room: its exact outline (traced edge, simplified) and a label point, in robot pose units.

    Outlines follow the room's own pixels, so neighbouring rooms don't overlap and a tap
    near a doorway picks the room it actually lands in.
    """
    lay = state.layout
    if lay is None or "origin" not in getattr(lay, "header", {}):
        return {}
    by_room: dict[int, set[tuple[int, int]]] = {}
    for y in range(lay.height):
        row = lay.pixels[y * lay.width:(y + 1) * lay.width]
        for x, b in enumerate(row):
            if (b >> 2) <= 31:
                by_room.setdefault(b >> 2, set()).add((x, y))
    shapes: dict[int, dict] = {}
    for room, cells in by_room.items():
        # largest 4-connected part (ignore stray specks)
        seen: set[tuple[int, int]] = set()
        best: set[tuple[int, int]] = set()
        for c in cells:
            if c in seen:
                continue
            blob, stack = set(), [c]
            while stack:
                p = stack.pop()
                if p in seen or p not in cells:
                    continue
                seen.add(p)
                blob.add(p)
                stack += [(p[0] + 1, p[1]), (p[0] - 1, p[1]), (p[0], p[1] + 1), (p[0], p[1] - 1)]
            if len(blob) > len(best):
                best = blob
        if len(best) < 4:
            continue
        outline = _simplify([(float(x), float(y)) for x, y in _trace(best)], eps)
        # label: the room pixel deepest inside (farthest from any non-room pixel), sampled on a coarse grid
        def depth(p: tuple[int, int]) -> int:
            r = 0
            while all((p[0] + dx, p[1] + dy) in best for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r))):
                r += 1
            return r
        label = max(sorted(best)[::7] or sorted(best), key=depth)
        shapes[room] = {"outline": [list(state.to_pose(p)) for p in outline],
                        "centre": list(state.to_pose((float(label[0]), float(label[1]))))}
    return shapes