# Changelog

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
