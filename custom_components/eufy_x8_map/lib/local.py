"""Local (LAN) link to the Eufy X8 over Tuya protocol 3.3 (ha-eufy-x8-map, Apache-2.0). HA-free.

- Frames: 000055AA | seq | cmd | len | [payload] | crc32 | 0000AA55.
- Payloads are AES-128-ECB with the robot's local key. Commands (CONTROL) carry a
  "3.3" + 12-byte header; queries and heartbeats don't.
- The robot pushes STATUS frames whenever a DPS changes (seen in the app capture of
  21 Nov 2026: 2, 5, 15, 101, 104, 122, 124 ...).
- Discovery: the robot broadcasts on UDP 6667, encrypted with Tuya's public UDP key.

The local key is passed in, kept in memory only, and never logged or returned.
"""
from __future__ import annotations

import asyncio
import binascii
import hashlib
import json
import logging
import struct
import time
from typing import Any, Callable

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .capture import PREFIX, SUFFIX, decrypt_payload

_LOGGER = logging.getLogger(__name__)

PORT = 6668
DISCOVERY_PORT = 6667
CONTROL, STATUS, HEART_BEAT, DP_QUERY, UPDATE_DPS = 7, 8, 9, 10, 18
NO_HEADER = {HEART_BEAT, DP_QUERY, 16, 18}
HEADER_33 = b"3.3" + b"\0" * 12
UDP_KEY = hashlib.md5(b"yGAdlopoPVldABfn").digest()  # Tuya's published broadcast key, not a secret
HEARTBEAT_S = 10
READ_TIMEOUT_S = 30


def encrypt(key: bytes, obj: Any) -> bytes:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    pad = 16 - len(raw) % 16
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(raw + bytes([pad]) * pad) + enc.finalize()


def build_frame(seq: int, cmd: int, payload: bytes) -> bytes:
    head = PREFIX + struct.pack(">III", seq, cmd, len(payload) + 8)
    body = head + payload
    return body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF) + SUFFIX


def make_message(key: bytes, seq: int, cmd: int, obj: Any) -> bytes:
    payload = encrypt(key, obj)
    if cmd not in NO_HEADER:
        payload = HEADER_33 + payload
    return build_frame(seq, cmd, payload)


def split_frames(buffer: bytes) -> tuple[list[tuple[int, int, bytes]], bytes]:
    """Complete frames as (seq, cmd, body) and the unparsed rest. Bad CRC frames are skipped."""
    frames: list[tuple[int, int, bytes]] = []
    pos = 0
    while True:
        start = buffer.find(PREFIX, pos)
        if start == -1:
            return frames, b""
        if start + 16 > len(buffer):
            return frames, buffer[start:]
        seq, cmd, length = struct.unpack(">III", buffer[start + 4:start + 16])
        end = start + 16 + length
        if length > 65536:
            pos = start + 4
            continue
        if end > len(buffer):
            return frames, buffer[start:]
        frame = buffer[start:end]
        if frame[-4:] == SUFFIX and struct.unpack(">I", frame[-8:-4])[0] == binascii.crc32(frame[:-8]) & 0xFFFFFFFF:
            frames.append((seq, cmd, frame[16:-8]))
        pos = end


def control_obj(dev_id: str, dps: dict[str, Any]) -> dict[str, Any]:
    return {"devId": dev_id, "uid": dev_id, "t": str(int(time.time())), "dps": dps}


def query_obj(dev_id: str) -> dict[str, Any]:
    return {"gwId": dev_id, "devId": dev_id, "uid": dev_id, "t": str(int(time.time()))}


def parse_discovery(data: bytes) -> dict[str, Any] | None:
    for _seq, _cmd, body in split_frames(data)[0]:
        msg = decrypt_payload(body, UDP_KEY)
        if isinstance(msg, dict):
            return msg
    try:  # some firmwares send plain JSON
        msg = json.loads(data.decode())
        return msg if isinstance(msg, dict) else None
    except Exception:  # noqa: BLE001
        return None


async def discover(dev_id: str, timeout: float = 8.0) -> str | None:
    """Listen for the robot's UDP broadcast; return its IP or None."""
    loop = asyncio.get_running_loop()
    found: asyncio.Future[str] = loop.create_future()

    class Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple) -> None:
            msg = parse_discovery(data)
            if msg and msg.get("gwId") == dev_id and not found.done():
                found.set_result(msg.get("ip") or addr[0])

    try:
        transport, _ = await loop.create_datagram_endpoint(
            Proto, local_addr=("0.0.0.0", DISCOVERY_PORT), reuse_port=True, allow_broadcast=True)
    except OSError as err:
        _LOGGER.debug("Discovery port unavailable: %s", type(err).__name__)
        return None
    try:
        return await asyncio.wait_for(found, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        transport.close()


class LocalLink:
    """One TCP connection to the robot: query, heartbeat, live STATUS pushes, and CONTROL."""

    def __init__(self, host: str, dev_id: str, key: bytes, on_dps: Callable[[dict[str, Any]], None]) -> None:
        self.host, self.dev_id, self._key, self._on_dps = host, dev_id, key[:16], on_dps
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._seq = 0
        self._acks: dict[int, asyncio.Future] = {}
        self.connected = False
        self.last_seen: float | None = None
        self.listeners: list[Callable[[int, dict[str, Any]], None]] = []

    def _next(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq

    async def _write(self, cmd: int, obj: Any) -> int:
        if not self._writer:
            raise ConnectionError("not connected")
        seq = self._next()
        self._writer.write(make_message(self._key, seq, cmd, obj))
        await self._writer.drain()
        return seq

    async def run(self, port: int = PORT) -> None:
        """Connect and read until the connection drops. Raises on failure (caller retries)."""
        self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(self.host, port), 8)
        self.connected = True
        beat = asyncio.ensure_future(self._heartbeat())
        try:
            await self._write(DP_QUERY, query_obj(self.dev_id))
            buffer = b""
            while True:
                chunk = await asyncio.wait_for(self._reader.read(4096), READ_TIMEOUT_S)
                if not chunk:
                    raise ConnectionError("closed by robot")
                frames, buffer = split_frames(buffer + chunk)
                for seq, cmd, body in frames:
                    self._handle(seq, cmd, body)
        finally:
            beat.cancel()
            await self.close()

    def _handle(self, seq: int, cmd: int, body: bytes) -> None:
        self.last_seen = time.monotonic()
        ack = self._acks.pop(seq, None)
        if ack and not ack.done():
            ack.set_result(body[:4])
        msg = decrypt_payload(body, self._key) if len(body) > 4 else None
        if isinstance(msg, dict):
            dps = msg.get("dps") or (msg.get("data") or {}).get("dps")
            if isinstance(dps, dict) and dps:
                self._on_dps({str(k): v for k, v in dps.items()})
            for listener in list(self.listeners):
                listener(cmd, msg)

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                await self._write(HEART_BEAT, {"gwId": self.dev_id, "devId": self.dev_id})
            except Exception:  # noqa: BLE001
                return

    async def send(self, dps: dict[str, Any], timeout: float = 5.0) -> None:
        """CONTROL; waits for the robot's acknowledgement (return code 0)."""
        loop = asyncio.get_running_loop()
        seq = self._seq + 1
        ack: asyncio.Future = loop.create_future()
        self._acks[seq] = ack
        await self._write(CONTROL, control_obj(self.dev_id, dps))
        code = await asyncio.wait_for(ack, timeout)
        if code and code != b"\0\0\0\0":
            raise ConnectionError(f"robot refused (code {int.from_bytes(code, 'big')})")

    async def request_update(self, dp_ids: list[int]) -> None:
        """Ask the robot to re-report these DPS (Tuya UPDATEDPS; read-only)."""
        await self._write(UPDATE_DPS, {"dpId": [int(d) for d in dp_ids]})

    async def close(self) -> None:
        self.connected = False
        writer, self._writer = self._writer, None
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
