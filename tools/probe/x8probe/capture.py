"""Decode Eufy X8 local (Tuya v3.3) commands from a phone capture (ha-eufy-x8-map, Apache-2.0).

Reads a .pcap or .pcapng (e.g. from PCAPdroid), finds TCP traffic on port 6668,
splits it into Tuya frames (000055AA ... 0000AA55), decrypts payloads with the
device's local key (AES-128-ECB) and decodes DPS 124 base64 JSON. The key is
only used in memory; nothing here prints or saves it.
"""
from __future__ import annotations

import base64
import json
import struct
from typing import Any, Iterator

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PREFIX, SUFFIX = b"\x00\x00\x55\xaa", b"\x00\x00\xaa\x55"
TUYA_PORT = 6668
CMD_NAMES = {7: "control", 8: "status", 9: "heartbeat", 10: "query", 13: "control_new", 16: "query_new", 18: "update_dps"}


# --- pcap / pcapng -----------------------------------------------------------
def _packets(data: bytes) -> Iterator[tuple[float, int, bytes]]:
    """Yield (time, linktype, frame) from pcap or pcapng."""
    magic = data[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"):
        e = "<" if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
        nano = magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
        link = struct.unpack(e + "I", data[20:24])[0]
        pos = 24
        while pos + 16 <= len(data):
            sec, frac, incl, _ = struct.unpack(e + "IIII", data[pos:pos + 16])
            pos += 16
            yield sec + frac / (1e9 if nano else 1e6), link, data[pos:pos + incl]
            pos += incl
        return
    if magic == b"\x0a\x0d\x0d\x0a":  # pcapng
        pos, links, e = 0, [], "<"
        while pos + 12 <= len(data):
            btype = struct.unpack(e + "I", data[pos:pos + 4])[0]
            if btype == 0x0A0D0D0A:
                e = "<" if data[pos + 8:pos + 12] == b"\x4d\x3c\x2b\x1a" else ">"
            blen = struct.unpack(e + "I", data[pos + 4:pos + 8])[0]
            if blen < 12:
                break
            body = data[pos + 8:pos + blen - 4]
            if btype == 1:
                links.append(struct.unpack(e + "H", body[:2])[0])
            elif btype == 6:
                iface, hi, lo, cap = struct.unpack(e + "IIII", body[:16])
                yield ((hi << 32) | lo) / 1e6, links[iface] if iface < len(links) else 1, body[20:20 + cap]
            pos += blen
        return
    raise ValueError("not a pcap/pcapng file")


def _ip_tcp(link: int, frame: bytes) -> tuple[str, int, str, int, bytes] | None:
    """Return (src, sport, dst, dport, payload) for TCP, else None."""
    if link == 1:  # Ethernet
        if len(frame) < 14:
            return None
        etype, off = struct.unpack(">H", frame[12:14])[0], 14
        if etype == 0x8100:
            etype, off = struct.unpack(">H", frame[16:18])[0], 18
        frame = frame[off:]
    elif link == 113:  # Linux cooked
        frame = frame[16:]
    if not frame:
        return None
    version = frame[0] >> 4
    if version == 4:
        ihl = (frame[0] & 0x0F) * 4
        if frame[9] != 6:
            return None
        total = struct.unpack(">H", frame[2:4])[0]
        src, dst, seg = ".".join(map(str, frame[12:16])), ".".join(map(str, frame[16:20])), frame[ihl:total]
    elif version == 6:
        if frame[6] != 6:
            return None
        src, dst, seg = frame[8:24].hex(), frame[24:40].hex(), frame[40:40 + struct.unpack(">H", frame[4:6])[0]]
    else:
        return None
    if len(seg) < 20:
        return None
    sport, dport = struct.unpack(">HH", seg[:4])
    return src, sport, dst, dport, seg[(seg[12] >> 4) * 4:]


def tcp_streams(data: bytes, port: int = TUYA_PORT) -> list[dict[str, Any]]:
    """Payload chunks to/from the Tuya port, in capture order."""
    out = []
    for t, link, frame in _packets(data):
        parsed = _ip_tcp(link, frame)
        if not parsed:
            continue
        src, sport, dst, dport, payload = parsed
        if payload and port in (sport, dport):
            out.append({"t": t, "to_robot": dport == port, "flow": (src, sport, dst, dport), "data": payload})
    return out


# --- Tuya frames ---------------------------------------------------------------
def tuya_frames(stream: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (cmd, payload) for each 000055AA frame in a byte stream."""
    pos = 0
    while (start := stream.find(PREFIX, pos)) != -1 and start + 16 <= len(stream):
        _seq, cmd, length = struct.unpack(">III", stream[start + 4:start + 16])
        end = start + 16 + length
        if end > len(stream) or stream[end - 4:end] != SUFFIX:
            pos = start + 4
            continue
        body = stream[start + 16:end - 8]  # drop crc + suffix
        yield cmd, body
        pos = end


def decrypt_payload(body: bytes, key: bytes) -> Any:
    """Decrypt a v3.3 payload (optionally behind '3.3' + 12 bytes, or a 4-byte return code)."""
    for candidate in (body[15:] if body[:3] == b"3.3" else None, body[4:], body):
        if not candidate or len(candidate) % 16:
            continue
        try:
            dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
            plain = dec.update(candidate) + dec.finalize()
            pad = plain[-1]
            if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
                plain = plain[:-pad]
            return json.loads(plain.decode())
        except Exception:  # noqa: BLE001
            continue
    return None


def flows_from_capture(data: bytes) -> list[dict[str, Any]]:
    """Group the Tuya-port TCP payloads into flows: [{"to_robot", "t", "data"(bytes)}]."""
    flows: dict[tuple, dict[str, Any]] = {}
    for chunk in tcp_streams(data):
        flow = flows.setdefault(chunk["flow"], {"to_robot": chunk["to_robot"], "t": chunk["t"], "data": bytearray()})
        flow["data"] += chunk["data"]
    return [{**f, "data": bytes(f["data"])} for f in flows.values()]


def decode_flows(flows: list[dict[str, Any]], local_key: str) -> list[dict[str, Any]]:
    """Decrypt Tuya frames in each flow and unpack DPS 124. No key in the output."""
    key = local_key.encode()[:16]
    messages: list[dict[str, Any]] = []
    for flow in flows:
        for cmd, body in tuya_frames(flow["data"]):
            msg = decrypt_payload(body, key)
            if not isinstance(msg, dict):
                continue
            dps = msg.get("dps") or (msg.get("data") or {}).get("dps") or {}
            entry: dict[str, Any] = {"direction": "app->robot" if flow["to_robot"] else "robot->app",
                                     "cmd": CMD_NAMES.get(cmd, cmd), "t": msg.get("t") or flow.get("t"), "dps": {}}
            for dp, value in dps.items():
                decoded = None
                if isinstance(value, str):
                    try:
                        decoded = json.loads(base64.b64decode(value, validate=True).decode())
                    except Exception:  # noqa: BLE001
                        decoded = None
                entry["dps"][str(dp)] = decoded if decoded is not None else value
            if entry["dps"]:
                messages.append(entry)
    messages.sort(key=lambda m: (m["t"] or 0))
    return messages


def decode_capture(data: bytes, local_key: str) -> list[dict[str, Any]]:
    """Decoded Tuya messages (app <-> robot) from a pcap/pcapng."""
    return decode_flows(flows_from_capture(data), local_key)
