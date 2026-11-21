# ha-eufy-x8-map

Floor-plan maps for the **Eufy RoboVac X8** in Home Assistant. Work in progress.

The X8 builds a SLAM map, but no existing Home Assistant integration shows it. This project aims to add a live map (rooms, dock, robot position and cleaning trail) that works **alongside** the integration you already use for control.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Read-only probe: does the cloud map method work on the X8? | **Done.** Map and live position both work on the T2262A |
| 2 | Integration: map camera, current room, route, room names, robot status | **Done (0.2.x)** |
| 3 | Room cleaning and spot cleaning | **0.5.0:** `clean_rooms` and `spot_clean` services, built from formats captured from the Eufy app. Awaiting first supervised test. Plain goto stays disabled |
| 3b | Local control: LAN link, local sensors, room buttons | **Done (0.6.x)**, tested on the robot |
| 4 | Fully local map (no Eufy cloud) | **Researched 21 Nov 2026:** the X8 sends its map and position only to the Eufy cloud; it ignores map requests on the LAN. HA keeps a saved copy of the room map and the LAN key (0.7.x), so control, sensors and the static map work without the cloud. Live position still needs the cloud |

Phase 1–2 fetch the map from Eufy's cloud (the room layout file `lay.bin` plus live position frames). Robot control stays local with your current integration. The long-term goal is to drop the cloud.

## Install (Home Assistant Marketplace, formerly HACS)

1. Marketplace → ⋮ → **Custom repositories** → add `https://github.com/mcpannett/ha-eufy-x8-map` as **Integration**.
2. Install **Eufy X8 Map** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → **Eufy X8 Map**, then log in with your Eufy account. The password is used once and **not stored**: only your Eufy account ID and region are kept.
4. Optional: open the integration's **Configure** and pick your existing vacuum entity, so the map switches to fast updates the moment it starts cleaning.

**Entities**
- `camera.<name>_map_map`: floor plan with room colours, walls, dock (red), robot (blue) and this clean's route (white).
- `sensor.<name>_map_current_room`: the room the robot is in (`Room 1`–`Room n`, numbered as the X8 numbers them).
- `sensor.<name>_map_robot_status`: the X8's own status (Charging, Running, Recharge…).
- `button.<name>_map_refresh_map`: re-download the map now (it also refreshes by itself when you edit the map in the app).
- Diagnostic: route points, map activity (`live` / `idle`), and **Record app commands** (switch) + **Recorded app commands** (sensor): turn the switch on, use the Eufy app (clean a room, go to a spot, draw a zone), and the decoded commands appear in the sensor's attributes. Read-only.

**Cloud use:** about every 2 s while the robot moves (position) and every 20 s (map). While docked, once a minute (to spot a new clean) and the map hourly. Robot control stays with your existing integration.

## Dashboard card

```yaml
type: picture-entity
entity: camera.robovac_map_map
camera_view: live
show_name: false
show_state: false
```

Add an entities card with `sensor.robovac_map_current_room` and `sensor.robovac_map_robot_status` under it.

## Phase 1 probe

Read-only: logs in, lists your vacuums and model codes, downloads the floor plan to `map.png`, and optionally records the live route to `route.png`. It sends no commands to the robot. Your password is typed locally and never saved or printed. See [`tools/probe/README.txt`](tools/probe/README.txt).

```
pip install requests cryptography
cd tools/probe
python probe.py --watch 10
```

## How the map is fetched

1. `thing.m.dev.storage.config.get` returns short-lived storage credentials and a per-device path.
2. `<path>/common/layout/lay.bin` holds the layout: 24-byte header (`0pam`, width, height, dock) + raw LZ4 block.
3. One byte per pixel: high 6 bits room id (0–31), low 2 bits pixel type.
4. Live position: `tuya.m.device.media.latest` pose frames (protobuf, zigzag x/y offsets from the dock, 1/10 pixel).

On the X8 (T2262A) the layout uses a Tuya type-0 header (width, height, signed origin, LZ4) and live poses map to pixels as `(pose − origin) / 10`; pose (0, 0) is the dock. Verified on 20 Nov 2026: 212 of 213 live points landed on the floor plan.

## Credits and licence

The Eufy/Tuya client code and the map method come from [damacus/robovac](https://github.com/damacus/robovac) (Apache-2.0), including PR #636 by stevendejongnl, building on Andre Borie's eufy-device-id-and-local-key-grabber. This repository is licensed under Apache-2.0 (see `LICENSE`).

Unofficial. Not affiliated with Anker/Eufy or Tuya.
