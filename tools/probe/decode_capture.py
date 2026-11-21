"""Decode Eufy X8 app commands from a PCAPdroid capture (read-only).

Put the .pcap / .pcapng in this folder, run this script, log in when asked.
It fetches the X8's local key from your Eufy account (kept in memory only),
decrypts the app<->robot messages in the capture, and writes them to
x8probe-output/capture-decoded.json. The key is never printed or saved.
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

from probe import OUT, login, short_id
from x8probe.capture import decode_capture


def main() -> None:
    here = Path(__file__).resolve().parent
    captures = sorted([*here.glob("*.pcap"), *here.glob("*.pcapng")], key=lambda p: p.stat().st_mtime)
    if len(sys.argv) > 1:
        captures = [Path(sys.argv[1])]
    if not captures:
        sys.exit(f"No .pcap or .pcapng file found in {here}. Copy the PCAPdroid file here first.")
    capture = captures[-1]
    print(f"Capture: {capture.name} ({capture.stat().st_size} bytes)")
    email = input("Eufy account email: ").strip()
    password = getpass.getpass("Eufy password (hidden, not saved): ")
    session, vacs = login(email, password)
    del password
    vac = vacs[0] if len(vacs) == 1 else vacs[int(input("Which vacuum number? ") or 0)]
    key = session._request(action="tuya.m.device.get", version="1.0", data={"devId": vac["id"]}).get("localKey", "")  # noqa: SLF001
    if not key:
        sys.exit("Could not get the local key from the account.")
    messages = decode_capture(capture.read_bytes(), key)
    del key
    OUT.mkdir(exist_ok=True)
    out = OUT / "capture-decoded.json"
    out.write_text(json.dumps({"capture": capture.name, "device": short_id(vac["id"]), "messages": messages}, indent=1))
    app = [m for m in messages if m["direction"] == "app->robot"]
    print(f"Decoded {len(messages)} messages ({len(app)} from the app) -> {out}")
    for m in app[:40]:
        print(" ", json.dumps(m["dps"])[:160])
    if not messages:
        print("Nothing decoded: was the phone on the same Wi-Fi as the X8 during the capture?")


if __name__ == "__main__":
    main()
