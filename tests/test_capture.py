"""End-to-end: build Tuya v3.3 frames, wrap them in pcap/pcapng, decode them back."""
import base64, json, struct, sys, zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "probe"))
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402
from x8probe.capture import decode_capture, tuya_frames  # noqa: E402

KEY = "0123456789abcdef"


def enc(obj, version_header=True):
    plain = json.dumps(obj).encode()
    pad = 16 - len(plain) % 16
    e = Cipher(algorithms.AES(KEY.encode()), modes.ECB()).encryptor()
    body = e.update(plain + bytes([pad]) * pad) + e.finalize()
    return (b"3.3" + b"\x00" * 12 + body) if version_header else body


def frame(cmd, body, seq=1):
    payload = body + struct.pack(">I", zlib.crc32(body)) + b"\x00\x00\xaa\x55"
    return b"\x00\x00\x55\xaa" + struct.pack(">III", seq, cmd, len(payload)) + payload


def ipv4_tcp(src, dst, sport, dport, payload):
    tcp = struct.pack(">HHIIBBHHH", sport, dport, 0, 0, 5 << 4, 0x18, 65535, 0, 0) + payload
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0, bytes(map(int, src.split("."))), bytes(map(int, dst.split("."))))
    return ip + tcp


def pcap(packets, link=101):
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, link)
    for i, p in enumerate(packets):
        out += struct.pack("<IIII", 1000 + i, 0, len(p), len(p)) + p
    return out


def pcapng(packets, link=101):
    def block(t, body):
        body += b"\x00" * (-len(body) % 4)
        n = 12 + len(body)
        return struct.pack("<II", t, n) + body + struct.pack("<I", n)
    out = block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    out += block(1, struct.pack("<HHI", link, 0, 65535))
    for p in packets:
        out += block(6, struct.pack("<IIIII", 0, 0, 0, len(p), len(p)) + p)
    return out


def conversation():
    room_cmd = base64.b64encode(json.dumps({"method": "roomClean", "data": {"roomIds": [5]}, "timestamp": 1}).encode()).decode()
    echo = base64.b64encode(json.dumps({"method": "roomClean", "result": "O", "timestamp": 1}).encode()).decode()
    app = frame(7, enc({"devId": "d", "t": 1700000000, "dps": {"124": room_cmd}}))
    robot = frame(8, enc({"devId": "d", "dps": {"124": echo, "15": "Running"}, "t": 1700000001}))
    junk = b"\x01\x02noise"
    return [ipv4_tcp("192.168.1.20", "192.168.1.55", 50000, 6668, junk + app),
            ipv4_tcp("192.168.1.55", "192.168.1.20", 6668, 50000, robot),
            ipv4_tcp("192.168.1.20", "8.8.8.8", 50001, 443, b"other")]


def check(messages):
    app = [m for m in messages if m["direction"] == "app->robot"]
    robot = [m for m in messages if m["direction"] == "robot->app"]
    assert app[0]["cmd"] == "control" and app[0]["dps"]["124"]["data"]["roomIds"] == [5]
    assert robot[0]["dps"]["124"]["result"] == "O" and robot[0]["dps"]["15"] == "Running"
    assert KEY not in json.dumps(messages)


def test_pcap_raw_ip():
    check(decode_capture(pcap(conversation()), KEY))


def test_pcapng_ethernet():
    eth = [b"\x00" * 12 + b"\x08\x00" + p for p in conversation()]
    check(decode_capture(pcapng(eth, link=1), KEY))


def test_wrong_key_decodes_nothing():
    assert decode_capture(pcap(conversation()), "ffffffffffffffff") == []


def test_frame_without_version_header():
    body = enc({"dps": {"15": "Charging"}}, version_header=False)
    assert list(tuya_frames(b"xx" + frame(8, body)))[0][0] == 8
