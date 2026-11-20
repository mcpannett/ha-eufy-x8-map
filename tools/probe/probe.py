"""Eufy X8 map probe (Phase 1, read-only).

Checks whether the X8 floor-plan method found for the T2276 (damacus/robovac
PR #636) also works on your X8. It:
  1. logs in to your Eufy account (password typed here, never saved or printed),
  2. lists your vacuums and their model codes,
  3. downloads the current room layout (lay.bin) and saves it as map.png,
  4. optionally (--watch MINUTES) records the live position while the robot
     cleans and draws the route on route.png.

It sends NO commands to the robot and changes no settings. Everything it saves
goes in ./x8probe-output. Credentials, tokens, keys and signed URLs are never
written to disk or printed.

Usage:
  pip install requests cryptography
  python probe.py              # map only
  python probe.py --watch 10   # map + 10 minutes of live position (start a clean first)
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import struct
import sys
import time
import zlib
from collections import Counter
from pathlib import Path

from x8probe.countries import (
    get_phone_code_by_country_code, get_phone_code_by_region,
    get_region_by_country_code, get_region_by_phone_code,
)
from x8probe.eufywebapi import EufyLogon
from x8probe.map_fetch import (
    ROOM_ID_MAX, ROOM_ID_OBSTACLE, MapFetchError, RouteTracker, decode_layout,
    fetch_frames, fetch_layout_bytes,
)
from x8probe.tuyawebapi import TuyaAPISession

OUT = Path("x8probe-output")


def short_id(value: str) -> str:
    """Stable, non-reversible label for a device id (so outputs carry no ids)."""
    return "dev-" + hashlib.sha256(value.encode()).hexdigest()[:8]


def login(email: str, password: str) -> tuple[TuyaAPISession, list[dict]]:
    """Eufy login -> Tuya session, same flow as damacus/robovac's config flow."""
    eufy = EufyLogon(email, password)
    resp = eufy.get_user_info()
    if resp is None or resp.status_code != 200:
        sys.exit("Could not reach Eufy login (network?).")
    user = resp.json()
    if user.get("res_code") != 1:
        sys.exit("Eufy login refused (check email/password).")
    info = user["user_info"]
    devices = eufy.get_device_info(info["request_host"], info["id"], user["access_token"]).json()
    settings = eufy.get_user_settings(info["request_host"], info["id"], user["access_token"]).json()
    tuya_home = settings.get("setting", {}).get("home_setting", {}).get("tuya_home", {})
    if "tuya_region_code" in tuya_home:
        region = tuya_home["tuya_region_code"]
        phone = info.get("phone_code") or get_phone_code_by_region(region)
    elif info.get("phone_code"):
        region, phone = get_region_by_phone_code(info["phone_code"]), info["phone_code"]
    elif info.get("country"):
        region, phone = get_region_by_country_code(info["country"]), get_phone_code_by_country_code(info["country"])
    else:
        region, phone = "EU", "44"
    session = TuyaAPISession(username="eh-" + info["id"], region=region, timezone=info["timezone"], phone_code=phone)
    vacs = [d for d in devices.get("devices", []) if d.get("product", {}).get("appliance") == "Cleaning"]
    return session, vacs


# --- tiny PNG writer (no Pillow needed) -------------------------------------
def write_png(path: Path, width: int, height: int, rgb: bytearray) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + bytes(rgb[y * width * 3:(y + 1) * width * 3]) for y in range(height))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


PALETTE = [(231, 111, 81), (42, 157, 143), (233, 196, 106), (38, 70, 83), (244, 162, 97),
           (138, 177, 125), (106, 76, 147), (25, 130, 196), (255, 89, 94), (138, 201, 38)]


def render(layout, route: list[list[tuple[float, float]]] | None = None, scale: int = 4) -> tuple[int, int, bytearray]:
    w, h = layout.width * scale, layout.height * scale
    rgb = bytearray(w * h * 3)
    for y in range(layout.height):
        for x in range(layout.width):
            room = layout.pixels[y * layout.width + x] >> 2
            colour = PALETTE[room % len(PALETTE)] if room <= ROOM_ID_MAX else (60, 60, 60) if room == ROOM_ID_OBSTACLE else (245, 245, 245)
            for dy in range(scale):
                base = ((y * scale + dy) * w + x * scale) * 3
                rgb[base:base + scale * 3] = bytes(colour) * scale

    def dot(px: float, py: float, colour: tuple[int, int, int], r: int) -> None:
        cx, cy = int(px * scale), int(py * scale)
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                if 0 <= xx < w and 0 <= yy < h:
                    rgb[(yy * w + xx) * 3:(yy * w + xx) * 3 + 3] = bytes(colour)

    for piece in route or []:
        for i, (px, py) in enumerate(piece):
            dot(px, py, (0, 0, 0), 1)
            if i:  # fill between points
                ox, oy = piece[i - 1]
                steps = int(max(abs(px - ox), abs(py - oy)) * scale) or 1
                for s in range(steps):
                    dot(ox + (px - ox) * s / steps, oy + (py - oy) * s / steps, (0, 0, 0), 0)
    if layout.dock:
        dot(layout.dock[0] / 10, layout.dock[1] / 10, (220, 0, 0), 4)
    return w, h, rgb


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Eufy X8 map probe")
    parser.add_argument("--watch", type=float, default=0, help="minutes to record live position (start a clean first)")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)

    email = input("Eufy account email: ").strip()
    password = getpass.getpass("Eufy password (hidden, not saved): ")
    session, vacs = login(email, password)
    del password
    if not vacs:
        sys.exit("No vacuums found on this account.")

    summary: dict = {"probe": "x8-map-phase1", "devices": []}
    for i, vac in enumerate(vacs):
        model = vac.get("product", {}).get("product_code", "?")
        print(f"[{i}] {vac.get('alias_name') or vac.get('name') or 'vacuum'}  model={model}  {short_id(vac['id'])}")
    pick = 0 if len(vacs) == 1 else int(input("Which vacuum number? ") or 0)
    vac = vacs[pick]
    dev_id, model = vac["id"], vac.get("product", {}).get("product_code", "?")
    result: dict = {"device": short_id(dev_id), "model": model}

    # 1. Layout
    try:
        raw = fetch_layout_bytes(session, dev_id)
        (OUT / "lay.bin").write_bytes(raw)
        result["layout_bytes"] = len(raw)
        result["layout_magic"] = raw[:4].hex()
        layout = decode_layout(raw)
        rooms = layout.room_summary()
        result.update(layout_ok=True, width=layout.width, height=layout.height, dock=layout.dock, rooms=rooms)
        write_png(OUT / "map.png", *render(layout))
        print(f"MAP OK: {layout.width}x{layout.height}, {len(rooms)} rooms -> {OUT / 'map.png'}")
    except MapFetchError as err:
        layout = None
        result.update(layout_ok=False, layout_error=str(err))
        print(f"MAP FAILED: {err}")
    except Exception as err:  # noqa: BLE001 - report the type only (messages may carry URLs)
        layout = None
        result.update(layout_ok=False, layout_error=type(err).__name__)
        print(f"MAP FAILED: {type(err).__name__}")

    # 2. Live position
    if args.watch > 0:
        print(f"Recording live position for {args.watch} min (poll every 1 s). Ctrl+C stops early.")
        tracker = RouteTracker()
        if layout and layout.dock:
            tracker.set_layout_dock(layout.dock)
        lengths: Counter = Counter()
        polls = errors = 0
        records: set[int] = set()
        end = time.time() + args.watch * 60
        with (OUT / "frames.jsonl").open("w") as log:
            try:
                while time.time() < end:
                    polls += 1
                    try:
                        record_id, frames = fetch_frames(session, dev_id)
                    except Exception as err:  # noqa: BLE001
                        errors += 1
                        log.write(json.dumps({"t": round(time.time(), 2), "error": type(err).__name__}) + "\n")
                        time.sleep(2)
                        continue
                    if record_id:
                        records.add(record_id)
                    for f in frames:
                        lengths[len(f) // 2] += 1
                    log.write(json.dumps({"t": round(time.time(), 2), "record": record_id, "frames": frames}) + "\n")
                    tracker.update(record_id, frames)
                    if polls % 30 == 0:
                        print(f"  {polls} polls, {tracker.point_count} route points")
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        result["watch"] = {"polls": polls, "errors": errors, "clean_records": len(records),
                           "route_points": tracker.point_count, "frame_lengths": dict(lengths)}
        if layout:
            pieces = tracker.segments(layout.width, layout.height)
            on_map = sum(len(p) for p in pieces)
            result["watch"].update(route_pieces=len(pieces), points_on_map=on_map)
            write_png(OUT / "route.png", *render(layout, pieces))
            print(f"ROUTE: {tracker.point_count} points, {on_map} on the map -> {OUT / 'route.png'}")

    summary["devices"].append(result)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"Done. Send back the files in {OUT}/ (map.png, route.png, summary.json, lay.bin, frames.jsonl).")


if __name__ == "__main__":
    main()
