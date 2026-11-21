# Changelog

## 0.7.0 (21 Nov 2026) - milestone 3, part 1
- Offline map: the room layout is saved in HA after each download and loaded at start-up, so the map, rooms and buttons work when the Eufy cloud is down. Camera shows `map_source` and `cloud_error`.
- Cloud outages no longer stop the integration when a saved map or the LAN link is available.
- `local_listen` research service: asks the robot to re-report DPS over the LAN and, optionally, sends the app's DPS 121 map request, listing everything the robot sends back.

## 0.6.1 (21 Nov 2026)
- New local sensors: Clean time (DPS 109), Clean area (DPS 110), Run code (DPS 137, diagnostic; 24 = room unreachable, seen with a closed door).
- Tested on the robot over the LAN: Hallway room clean, Hallway spot clean, Hallway + Kitchen multi-room. Map unchanged after each.

## 0.6.0 (21 Nov 2026) - milestone 2, part 1: local link
- New **Local link** switch (off by default): a LAN connection to the robot (Tuya 3.3, port 6668). The robot is found by UDP discovery, or set its IP in options. The key is fetched into memory only.
- Commands go over the LAN when the link is up, with the cloud as fallback. Each send records its route.
- Live local sensors: Battery, Suction, Clean mode, and Robot status (local when the link is up). Diagnostic **Local link status** sensor lists other DPS for decoding later.
- One **Clean <room>** button per map room (two passes; needs Allow commands).
- `local_probe` service: read-only LAN check.

## 0.5.1 (21 Nov 2026)
- `clean_rooms` and `spot_clean` default to 2 passes (criss-cross). First supervised Bathroom test passed; map unchanged.

## 0.5.0 (21 Nov 2026)
- New service `clean_rooms`: rooms by name or number, 1-3 passes. Sends `selectRoomsClean` exactly as the Eufy app does (decoded from a capture).
- New service `spot_clean`: x/y in robot units or map pixels; the point must be inside a room. Sends `goto` with `target: spot`, as the app does.
- Both need 'Allow commands' on and keep the map guard. Plain goto stays disabled.

## 0.4.1 (21 Nov 2026)

- Capture decoder now reads the robot's replies (4-byte return code before the `3.3` header), not just the app's commands.
- **Confirmed from a real app capture:** return home = DPS 101 `true`; spot clean = DPS 124 `{"method":"goto","data":{"target":"spot","cleanTimes":1,"x":…,"y":…}}`. Its x/y use the same frame as the live position, so map pixel = (x − origin) / 10.

## 0.4.0 (21 Nov 2026)

- **Decode capture** service (read-only, returns a response): decrypts captured Eufy-app traffic inside Home Assistant using the account session, so no password is typed anywhere and the robot's local key never leaves HA. Input is the Tuya-port flows extracted from a PCAPdroid capture.

## 0.3.2 (20 Nov 2026)

- **Icon:** original light and dark brand images in `brand/` (shown by Home Assistant 2026.3+).
- `return_home` is refused while the robot is docked.
- **Record app commands** turns itself off after 6 hours, so a forgotten recorder doesn't keep polling the cloud.
- README: Marketplace wording and a dashboard card example.

## 0.3.1 (20 Nov 2026) — safety fixes after an incident

**What happened:** during testing, a `goto` was sent in coordinates taken from the live-position data, which the X8 does not use for goto (the eufy-x8 project had warned about this). The robot left the dock, could not place itself, returned, and came back on a plain map: the room division (and names) was lost. The map shape survived.

**Changes:**
- `goto` is **disabled** (service and command list) until its coordinate system is verified from a capture of the Eufy app.
- **Map guard:** commands are only sent once the map version is known; if the map version changes after a test command, **Allow commands switches itself off** and the event is logged in Recorded app commands.
- Rule for future tests: no movement command whose format and coordinates are not taken from a real app capture.

## 0.3.0 (20 Nov 2026)

- **Test-command layer** (for building room cleaning and tap-to-go), sent through Eufy's cloud so it never takes the robot's single local connection from your control integration:
  - `eufy_x8_map.return_home`, `eufy_x8_map.goto` (robot units or map pixels; sends `clear`, waits 35 s, then `goto`, as the app does), `eufy_x8_map.command` (DPS 124 method + data).
  - **Allow commands** switch (config, **off by default**): every service refuses while it is off.
  - Safety list: only goto/clear/stop/pause/resume/home and clean/room/zone methods; anything that could change or lose the map, settings or firmware (divide, merge, rename, reset, delete, save, map, OTA, Wi-Fi, set…) is always refused.
  - Every sent command is logged and shown in Recorded app commands (source `sent`), next to the robot's echo.

## Tools (20 Nov 2026)

- `decode_capture.bat` / `tools/probe/decode_capture.py`: decrypt Eufy app ↔ X8 messages from a PCAPdroid capture (pcap or pcapng; Tuya v3.3, AES-ECB with the local key held in memory only). Guide: `docs/capture-guide.md`.
- Recorder finding: the cloud DP history (`tuya.m.smart.operate.all.log`) holds DPS 124 echoes with method names (`clear`, `divideRoom`, `mergeRooms`, `renameRoom`) and results (S sent, O ok, F failed), but no command data.

## 0.2.2 (20 Nov 2026)

- Current room shows **Docked** when the X8 reports Charging or Sleeping, including straight after an HA restart (it was "unknown" until the robot moved).

## 0.2.1 (20 Nov 2026)

- **Room names:** set them under Configure as `number=name` (e.g. `1=Living Room, 2=Hallway`). Used by the current-room sensor and listed in the camera's `room_names` attribute.
- **Map layout:** new default "Match the Eufy app" (the app shows the map transposed). "As stored by the robot" keeps the old view.
- Options form keeps the recorder setting when saved.

## 0.2.0 (20 Nov 2026)

- **Map edits show up fast:** the integration reads the X8's map version (DPS 125) every minute and re-downloads the map as soon as it changes. New **Refresh map** button.
- **Robot status** sensor (X8 work status: Charging, Running, Recharge, …).
- **Record app commands** (diagnostic switch, off by default; read-only, sends nothing): records the commands the Eufy app sends (DPS 124 echoes), decoded, in the **Recorded app commands** sensor. Also tries the cloud DP history for the last 48 h. This is groundwork for room cleaning and tap-to-go.
- Only DPS 15/122/124/125/142 are kept from the device record; the local key never leaves the client.
- Options changes no longer reload the integration.

## 0.1.2 (20 Nov 2026)

- Walls drawn light grey so they show on dark dashboards.
- Current room reads **Docked** while the robot sits at the dock.
- Confirmed on hardware: 0.1.1 loads and decodes the X8 map after a clean (256×190, 5 rooms).

## 0.1.1 (20 Nov 2026)

- **Fix:** a failed map download no longer stops set-up. Position, room and route keep working, and the map retries every minute.
- The real reason for a map failure is now shown (camera attribute `map_error` and one log warning). These messages hold no URLs, tokens or IDs.
- Map decoder accepts more X8 variants: uncompressed layouts and other header type values.

## 0.1.0 (20 Nov 2026)

- **Home Assistant integration (HACS):** map camera (rooms, walls, dock, robot, route), current-room sensor, route-points and map-activity diagnostics.
- Login once in the config flow; the password is never stored (the cloud session only needs the account ID).
- Fast polling only while the robot is moving (or the linked vacuum entity is cleaning); slow while docked.
- Errors are reported by type only, so no signed URLs or tokens reach the logs.
- X8 coordinates solved: `pixel = (pose − origin) / 10`, pose (0, 0) = dock. Route is kept as raw poses so it stays correct when the map grows.
- Tests for the engine and renderer; CI adds hassfest and an import check against Home Assistant.

## 0.0.2 (20 Nov 2026)

- X8 (T2262A) layout decoded: Tuya type-0 header + LZ4, same room-per-pixel encoding. First real floor plan rendered (5 rooms).
- Probe 1b: lists the device's cloud storage and watches which map/route files change during a clean (live position from `media.latest` stayed static on the X8).

## 0.0.1 (20 Nov 2026)

- Phase 1 read-only probe (`tools/probe`): Eufy login, vacuum and model list, floor-plan download and decode to PNG, optional live route recording. No robot commands.
- Offline test for the map decoder, route tracker and PNG writer; GitHub CI.
