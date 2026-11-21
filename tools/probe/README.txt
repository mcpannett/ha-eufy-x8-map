Eufy X8 map probe - Phase 1 (read-only)
=======================================

What it does
- Logs in to your Eufy account. Your password is typed in this window only; it is never saved or shown.
- Shows your vacuums and their model codes.
- Downloads the X8's current floor plan and saves it as map.png.
- Optional: records the robot's live position while it cleans and draws route.png.
- It sends NO commands to the robot and changes nothing.

How to run (Windows)
1. Install Python 3.11+ from python.org (tick "Add python.exe to PATH").
2. Unzip this folder, open it, click the address bar, type  cmd  and press Enter.
3. Run:   pip install requests cryptography
4. Map only:          python probe.py
   Map + live route:  start a clean in the Eufy app, then  python probe.py --watch 10
5. Send Claude everything in the new  x8probe-output  folder:
   summary.json, map.png, route.png (if made), lay.bin, frames.jsonl (if made).
   These hold no password, token or device ID. lay.bin and the PNGs are your floor plan.

Credits
x8probe/ (tuyawebapi, eufywebapi, countries, map_fetch) comes from damacus/robovac
(Apache-2.0, see LICENSE-robovac), including the map method from PR #636 by stevendejongnl.
