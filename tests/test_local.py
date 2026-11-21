import asyncio, json, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "eufy_x8_map"))
from lib.capture import decrypt_payload  # noqa: E402
from lib.local import (CONTROL, DP_QUERY, HEADER_33, STATUS, UDP_KEY, LocalLink, build_frame, encrypt,  # noqa: E402
                       make_message, parse_discovery, split_frames)

KEY = b"0123456789abcdef"


def test_frame_roundtrip_and_header():
    msg = make_message(KEY, 7, CONTROL, {"dps": {"124": "x"}})
    frames, rest = split_frames(b"junk" + msg + msg[:10])
    assert rest == msg[:10] and len(frames) == 1
    seq, cmd, body = frames[0]
    assert (seq, cmd) == (7, CONTROL) and body.startswith(HEADER_33)
    assert decrypt_payload(body, KEY) == {"dps": {"124": "x"}}
    q = split_frames(make_message(KEY, 8, DP_QUERY, {"a": 1}))[0][0]
    assert not q[2].startswith(b"3.3")


def test_bad_crc_skipped():
    msg = bytearray(make_message(KEY, 1, CONTROL, {}))
    msg[-6] ^= 1
    assert split_frames(bytes(msg))[0] == []


def test_discovery_decode():
    payload = b"\0\0\0\0" + encrypt(UDP_KEY, {"ip": "192.168.1.102", "gwId": "dev1", "version": "3.3"})
    assert parse_discovery(build_frame(0, 0x13, payload))["ip"] == "192.168.1.102"


def test_link_against_fake_robot():
    async def main():
        got, sent = {}, []

        async def robot(reader, writer):
            buf = b""
            while True:
                data = await reader.read(4096)
                if not data:
                    return
                frames, buf = split_frames(buf + data)
                for seq, cmd, body in frames:
                    msg = decrypt_payload(body, KEY)
                    if cmd == DP_QUERY:
                        writer.write(build_frame(seq, DP_QUERY, b"\0\0\0\0" + encrypt(KEY, {"dps": {"15": "Charging", "104": 91}})))
                    elif cmd == CONTROL:
                        sent.append(msg["dps"])
                        writer.write(build_frame(seq, CONTROL, b"\0\0\0\0"))
                        writer.write(build_frame(0, STATUS, b"\0\0\0\0" + HEADER_33 + encrypt(KEY, {"dps": {"15": "Running"}, "t": 1})))
                    await writer.drain()

        server = await asyncio.start_server(robot, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        link = LocalLink("127.0.0.1", "dev1", KEY, got.update)
        task = asyncio.ensure_future(link.run(port))
        for _ in range(50):
            if got:
                break
            await asyncio.sleep(0.02)
        assert got == {"15": "Charging", "104": 91}
        await link.send({"124": "abc"})
        await asyncio.sleep(0.1)
        assert sent == [{"124": "abc"}] and got["15"] == "Running"
        task.cancel()
        await link.close()
        server.close()

    asyncio.run(main())
