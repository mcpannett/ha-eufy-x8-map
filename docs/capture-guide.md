# Capturing X8 app commands (for room cleaning and tap-to-go)

The X8's cloud history only keeps each command's name and result. To build room
cleaning and tap-to-go we need the full command the Eufy app sends. About 10 minutes.

## On your phone (Android)
1. Install **PCAPdroid** (Play Store, free, no root).
2. PCAPdroid → **Target apps** → pick **eufy Clean** only.
3. Dump mode → **PCAP file**. Keep **TLS decryption off** (not needed).
4. Phone on the **same Wi-Fi as the X8**. Press **Start** (accept the VPN prompt).
5. In the Eufy app, about a minute apart, and note what you did and when:
   1. Clean **one room** (e.g. Bathroom)
   2. Clean **two rooms** (e.g. Hallway + Kitchen)
   3. **Go to** a spot, then send it **home**
   4. Draw one **zone** and clean it, then send it home
6. PCAPdroid → **Stop**. Save the `.pcap`.

## On the PC
7. Copy the `.pcap` into `Documents\ha-eufy-x8-map\tools\probe\`.
8. Double-click `decode_capture.bat` and log in when asked (password stays on the PC).
9. Tell Claude "decoded". The result is in `tools\probe\x8probe-output\capture-decoded.json`.

The capture only contains what the Eufy app sends. The decoder uses the robot's
local key in memory only and never prints or saves it.
