# Changelog

## 0.0.2 (20 Nov 2026)

- X8 (T2262A) layout decoded: Tuya type-0 header + LZ4, same room-per-pixel encoding. First real floor plan rendered (5 rooms).
- Probe 1b: lists the device's cloud storage and watches which map/route files change during a clean (live position from `media.latest` stayed static on the X8).

## 0.0.1 (20 Nov 2026)

- Phase 1 read-only probe (`tools/probe`): Eufy login, vacuum and model list, floor-plan download and decode to PNG, optional live route recording. No robot commands.
- Offline test for the map decoder, route tracker and PNG writer; GitHub CI.
