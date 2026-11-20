# ha-eufy-x8-map

Floor-plan maps for the **Eufy RoboVac X8** in Home Assistant. Work in progress.

The X8 builds a SLAM map, but no existing Home Assistant integration shows it. This project aims to add a live map (rooms, dock, robot position and cleaning trail) that works **alongside** the integration you already use for control.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Read-only probe: does the cloud map method work on the X8? | **Map: yes** (T2262A, Tuya type-0 header). **Live position: not yet** (cloud pose frame static); probe 1b looks for a route file |
| 2 | HACS integration: map camera, position sensors, room list | Planned |
| 3 | Controls from the map (tap-to-go, rooms) | Stretch |
| 4 | Fully local map (no Eufy cloud) via the robot's P2P channel | Research |

Phase 1–2 fetch the map from Eufy's cloud (the room layout file `lay.bin` plus live position frames). Robot control stays local with your current integration. The long-term goal is to drop the cloud.

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

Verified on a T2276 by the original author; **not yet verified on the X8 (T2261/T2262)** — that is what Phase 1 checks.

## Credits and licence

The Eufy/Tuya client code and the map method come from [damacus/robovac](https://github.com/damacus/robovac) (Apache-2.0), including PR #636 by stevendejongnl, building on Andre Borie's eufy-device-id-and-local-key-grabber. This repository is licensed under Apache-2.0 (see `LICENSE`).

Unofficial. Not affiliated with Anker/Eufy or Tuya.
