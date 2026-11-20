"""X8 additions (ha-eufy-x8-map, Apache-2.0).

- decode_any(): decodes both layout headers seen so far:
    * "0pam" wrapper (T2276, from damacus/robovac PR #636)
    * Tuya type-0 laser header (seen on the X8 T2262A, 20 Nov 2026):
        0 version, 1-2 map id, 3 type, 4-5 width, 6-7 height,
        8-9 / 10-11 origin x/y (signed), 12-13, 14-15, 16-17 (meaning TBC),
        18-19 compressed length, 20-23 width*height, 24.. raw LZ4 block.
  Pixels in both: one byte, high 6 bits room id (0-31), low 2 bits type;
  0xFF background, 0xFD wall, 0xFC other.
- Storage helpers: list and fetch any object under the device's storage path
  (read-only S3 GETs signed with the short-lived STS credentials). Nothing
  secret (keys, tokens, signed URLs) is returned to callers or printed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
import datetime
import hashlib
import hmac

import requests

from .map_fetch import MapFetchError, _lz4_block_decompress, decode_layout


@dataclass(frozen=True)
class AnyLayout:
    fmt: str
    width: int
    height: int
    pixels: bytes
    header: dict


def decode_any(raw: bytes) -> AnyLayout:
    if raw[:4] == b"0pam":
        lay = decode_layout(raw)
        return AnyLayout("0pam", lay.width, lay.height, lay.pixels, {"dock": lay.dock})
    if len(raw) < 24:
        raise MapFetchError(f"layout too short ({len(raw)} bytes)")
    be = lambda a, b, s=False: int.from_bytes(raw[a:b], "big", signed=s)  # noqa: E731
    w, h, clen, total = be(4, 6), be(6, 8), be(18, 20), be(20, 24)
    size = w * h
    header = {"version": raw[0], "map_id": be(1, 3), "type": raw[3], "origin": (be(8, 10, True), be(10, 12, True)),
              "f12": be(12, 14, True), "f14": be(14, 16, True), "f16": be(16, 18, True)}
    payload = raw[24:]
    if not w or not h or w > 4000 or h > 4000:
        raise MapFetchError(f"unknown layout header {raw[:24].hex()} ({len(raw)} bytes)")
    if clen == 0 or (len(payload) == size and clen in (0, size)):
        # Uncompressed variant: the pixels follow the header as-is.
        if len(payload) < size:
            raise MapFetchError(f"uncompressed layout short: {len(payload)} < {size} (header {raw[:24].hex()})")
        return AnyLayout("tuya0-raw", w, h, bytes(payload[:size]), header)
    if 24 + clen > len(raw):
        raise MapFetchError(f"layout truncated: need {24 + clen}, have {len(raw)} (header {raw[:24].hex()})")
    try:
        pixels = _lz4_block_decompress(payload[:clen], size)
    except MapFetchError:
        raise
    except Exception as err:  # noqa: BLE001 - malformed block
        raise MapFetchError(f"LZ4 failed ({type(err).__name__}); header {raw[:24].hex()}") from None
    if total not in (size, 0):
        header["declared_total"] = total
    return AnyLayout("tuya0", w, h, pixels, header)


# --- device storage (read-only) ---------------------------------------------
class Storage:
    def __init__(self, session: Any, dev_id: str) -> None:
        cfg = session._request(action="thing.m.dev.storage.config.get", version="1.0",  # noqa: SLF001
                               data={"type": "Common", "devId": dev_id})
        for key in ("ak", "sk", "token", "bucket", "endpoint", "region", "pathConfig"):
            if key not in cfg:
                raise MapFetchError(f"storage config missing {key!r}")
        self._cfg = cfg
        self.host = f"{cfg['bucket']}.{cfg['endpoint']}"
        self.root = cfg["pathConfig"]["common"].rstrip("/")
        self.path_keys = sorted(cfg["pathConfig"].keys())

    def _url(self, path: str, extra: dict[str, str] | None = None) -> str:
        c = self._cfg
        now = datetime.datetime.now(datetime.timezone.utc)
        amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        scope = f"{day}/{c['region']}/s3/aws4_request"
        params = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{c['ak']}/{scope}",
                  "X-Amz-Date": amz, "X-Amz-Expires": "300", "X-Amz-SignedHeaders": "host",
                  "X-Amz-Security-Token": c["token"], **(extra or {})}
        query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(params.items()))
        uri = quote(path, safe="/")
        creq = "\n".join(["GET", uri, query, f"host:{self.host}\n", "host", "UNSIGNED-PAYLOAD"])
        sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(creq.encode()).hexdigest()])
        k = ("AWS4" + c["sk"]).encode()
        for part in (day, c["region"], "s3", "aws4_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
        return f"https://{self.host}{uri}?{query}&X-Amz-Signature={sig}"

    def _get(self, path: str, extra: dict[str, str] | None = None) -> requests.Response | None:
        try:
            resp = requests.get(self._url(path, extra), timeout=15)
        except requests.RequestException:
            return None
        return resp

    def list(self, prefix: str | None = None, max_keys: int = 200) -> tuple[int, list[dict]]:
        """List objects under prefix (default: the device root). Returns (http status, objects)."""
        pre = (prefix or self.root).lstrip("/")
        resp = self._get("/", {"list-type": "2", "prefix": pre, "max-keys": str(max_keys)})
        if resp is None:
            return 0, []
        if resp.status_code != 200:
            return resp.status_code, []
        items = []
        for block in re.findall(r"<Contents>(.*?)</Contents>", resp.text, re.S):
            get = lambda tag: (re.search(rf"<{tag}>(.*?)</{tag}>", block) or [None, None])[1]  # noqa: E731
            items.append({"key": get("Key"), "size": int(get("Size") or 0), "modified": get("LastModified")})
        return 200, items

    def fetch(self, key_or_path: str) -> tuple[int, bytes]:
        path = key_or_path if key_or_path.startswith("/") else "/" + key_or_path
        resp = self._get(path)
        if resp is None:
            return 0, b""
        return resp.status_code, resp.content if resp.status_code == 200 else b""

    def redact(self, key: str, dev_id: str, label: str) -> str:
        return key.replace(self.root.strip("/"), "<root>").replace(dev_id, label)


# Paths worth checking even if listing is refused (Tuya laser sweeper names).
CANDIDATES = ["layout/lay.bin", "route/rou.bin", "navigation/nav.bin", "nav/nav.bin",
              "route/nav.bin", "layout/map.bin", "route/cleanPath.bin", "route/navPath.bin"]
