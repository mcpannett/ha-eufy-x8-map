import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "probe"))
import pytest
import probe
from x8probe import x8map
from x8probe.x8map import Storage, decode_any
from x8probe.map_fetch import MapFetchError


def lz4_literals(data: bytes) -> bytes:
    n = len(data); out = bytearray()
    if n >= 15:
        out.append(0xF0); rest = n - 15
        while rest >= 255: out.append(255); rest -= 255
        out.append(rest)
    else:
        out.append(n << 4)
    return bytes(out) + data


def tuya0(w=12, h=8):
    px = bytes(((1 if x < 6 else 2) << 2) if 0 < x < w - 1 and 0 < y < h - 1 else 0xFD for y in range(h) for x in range(w))
    block = lz4_literals(px)
    hdr = bytes([0, 0x49, 0, 0]) + w.to_bytes(2, "big") + h.to_bytes(2, "big") + (-2150).to_bytes(2, "big", signed=True) \
        + (-590).to_bytes(2, "big", signed=True) + b"\x00\x1f\xff\xb3\x00\x00" + len(block).to_bytes(2, "big") + (w * h).to_bytes(4, "big")
    return hdr + block, px


def test_decode_tuya0_header():
    raw, px = tuya0()
    lay = decode_any(raw)
    assert (lay.fmt, lay.width, lay.height, lay.pixels) == ("tuya0", 12, 8, px)
    assert lay.header["origin"] == (-2150, -590)


def test_decode_rejects_garbage():
    with pytest.raises(MapFetchError):
        decode_any(b"\x00\x01\x02\x03" + b"\x00" * 30)


class FakeSession:
    def _request(self, **kw):
        assert kw["action"] == "thing.m.dev.storage.config.get"
        return {"ak": "AKSECRET", "sk": "SKSECRET", "token": "TOKSECRET", "bucket": "b", "endpoint": "e.example",
                "region": "tuyaeu.com", "pathConfig": {"common": "/DEVID123/common/", "log": "/x"}}


class Resp:
    def __init__(self, code, text="", content=b""):
        self.status_code, self.text, self.content = code, text, content


def test_storage_list_and_fetch_redacts(monkeypatch):
    calls = []
    xml = "<ListBucketResult><Contents><Key>DEVID123/common/layout/lay.bin</Key><Size>3121</Size><LastModified>2026-11-20T16:30:00Z</LastModified></Contents></ListBucketResult>"
    def fake_get(url, timeout):
        calls.append(url)
        return Resp(200, xml) if "list-type=2" in url else Resp(200, content=b"DATA")
    monkeypatch.setattr(x8map.requests, "get", fake_get)
    st = Storage(FakeSession(), "DEVID123")
    status, objs = st.list()
    assert status == 200 and objs[0]["size"] == 3121
    assert st.redact(objs[0]["key"], "DEVID123", "dev-x") == "<root>/layout/lay.bin"
    assert st.fetch("/DEVID123/common/layout/lay.bin") == (200, b"DATA")
    assert all("X-Amz-Signature=" in u and "SKSECRET" not in u for u in calls)


def test_probe_main_dry_run(tmp_path, monkeypatch):
    raw, _ = tuya0()
    monkeypatch.setattr(probe, "OUT", tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_: "me@example.com")
    monkeypatch.setattr(probe.getpass, "getpass", lambda *_: "pw")
    monkeypatch.setattr(probe, "login", lambda e, p: (FakeSession(), [{"id": "DEVID123", "product": {"product_code": "T2262A"}}]))
    monkeypatch.setattr(probe, "fetch_layout_bytes", lambda s, d: raw)
    monkeypatch.setattr(x8map.requests, "get", lambda url, timeout: Resp(404))
    monkeypatch.setattr(sys, "argv", ["probe.py"])
    probe.main()
    summary = json.loads((tmp_path / "summary.json").read_text())
    dev = summary["devices"][0]
    assert dev["layout_ok"] and dev["layout_format"] == "tuya0" and dev["rooms"] == [1, 2]
    assert (tmp_path / "map.png").exists()
    text = (tmp_path / "summary.json").read_text()
    assert "DEVID123" not in text and "SECRET" not in text and "pw" not in text.replace("path_types", "")
