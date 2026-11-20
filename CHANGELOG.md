# Changelog

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
