"""App-command recorder for the Eufy X8 (ha-eufy-x8-map, Apache-2.0). HA-free.

The X8 carries its smart commands on DPS 124 as base64 JSON, e.g.
{"method": "goto", "data": {...}, "timestamp": ...}, and echoes each one back
with a result ("O" ok / "F" failed). DPS 125 holds map info
({"defaultID": N, "version": N}); a new version means the map was edited.

Only these DPS are ever kept: 15, 122, 124, 125, 142. Nothing else from the
device record (in particular its local key) leaves the client.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

KEEP_DPS = ("15", "122", "124", "125", "142")
HISTORY_ACTIONS = (  # tried in order; the first that answers is used
    ("tuya.m.smart.operate.all.log", "1.0"),
    ("tuya.m.smart.operate.log", "1.0"),
    ("tuya.m.device.dp.report.log", "1.0"),
)


def decode_b64_json(value: Any) -> Any:
    """Decode a base64 JSON DPS value; return None if it isn't one."""
    if not isinstance(value, str) or len(value) < 8:
        return None
    try:
        return json.loads(base64.b64decode(value, validate=True).decode())
    except Exception:  # noqa: BLE001
        return None


def pick_dps(device: dict[str, Any]) -> dict[str, Any]:
    """Keep only the DPS we use from a tuya.m.device.get result."""
    dps = device.get("dps") or {}
    return {k: dps[k] for k in KEEP_DPS if k in dps}


def history_entries(result: Any) -> list[dict[str, Any]]:
    """Find {dp, value, t} entries in a DP-log answer of unknown shape."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            dp = node.get("dpId", node.get("dpid", node.get("dp")))
            if dp is not None and "value" in node:
                t = node.get("timeStamp", node.get("timestamp", node.get("time", node.get("gmtCreate"))))
                found.append({"dp": str(dp), "value": node["value"], "t": t})
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(result)
    return found


@dataclass
class CommandRecorder:
    """Keeps the most recent distinct DPS 124/125 values, decoded."""

    max_items: int = 40
    items: list[dict[str, Any]] = field(default_factory=list)
    _seen: set[tuple[str, str]] = field(default_factory=set)

    def add(self, dp: str, value: Any, when: Any, source: str) -> bool:
        if dp not in ("124", "125", "142") or not isinstance(value, str):
            return False
        key = (dp, value)
        if key in self._seen:
            return False
        self._seen.add(key)
        decoded = decode_b64_json(value)
        item: dict[str, Any] = {"dp": dp, "time": when, "source": source}
        if isinstance(decoded, dict):
            item.update({k: decoded.get(k) for k in ("method", "data", "result", "timestamp") if k in decoded})
            if dp != "124":
                item["data"] = decoded
        else:
            item["raw"] = value[:200]
        self.items.insert(0, item)
        del self.items[self.max_items:]
        return True


def map_version(dps: dict[str, Any]) -> Any:
    """Map version from DPS 125, or None."""
    info = decode_b64_json(dps.get("125"))
    return (info.get("defaultID"), info.get("version")) if isinstance(info, dict) else None
