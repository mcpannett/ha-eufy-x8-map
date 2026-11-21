"""Care helpers for the Eufy X8 (ha-eufy-x8-map, Apache-2.0). HA-free.

- Consumables: DPS 116 reports hours used per part. Lifespans below were read from the Eufy app's
  Accessory Services page (Nov 2026) and match its "remaining hours" and % exactly.
- Top-up: which rooms haven't been cleaned for N days.
- Run log: a short history of finished cleans, with a 7-day summary.
"""
from __future__ import annotations

import json
from typing import Any

# code in DPS 116 -> (key, lifespan hours). SP and TR are also reported but not shown in the app.
CONSUMABLES: dict[str, tuple[str, int]] = {
    "SB": ("side_brush", 250),
    "RB": ("rolling_brush", 450),
    "FM": ("filter", 200),
    "SS": ("sensors", 35),
}
REPLACE_AT_PERCENT = 10
RUN_LOG_DAYS = 60
RUN_LOG_MAX = 200


def consumable_hours(raw: Any) -> dict[str, int]:
    """DPS 116 (JSON text or dict) -> {"SB": 81, ...} hours used. Unknown/bad input -> {}."""
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        duration = (data or {}).get("consumable", {}).get("duration", {})
        return {k: int(v) for k, v in duration.items() if k in CONSUMABLES and isinstance(v, (int, float))}
    except (ValueError, TypeError, AttributeError):
        return {}


def percent_left(used: float, life: int) -> int:
    """Same rounding as the app (floor): 81 h of 250 -> 67 %."""
    return max(0, min(100, int(100 * (life - used) / life)))


def consumables(raw: Any) -> dict[str, dict[str, int]]:
    """{"side_brush": {"used_h", "life_h", "left_h", "percent"}, ...}"""
    out = {}
    for code, used in consumable_hours(raw).items():
        key, life = CONSUMABLES[code]
        out[key] = {"used_h": used, "life_h": life, "left_h": max(0, life - used), "percent": percent_left(used, life)}
    return out


def rooms_due(rooms: list[int], last_cleaned: dict[int, float], days: float, now: float) -> list[int]:
    """Rooms not cleaned in the last `days` (never cleaned = due), oldest first."""
    cutoff = now - days * 86400
    due = [r for r in rooms if last_cleaned.get(r, 0.0) < cutoff]
    return sorted(due, key=lambda r: last_cleaned.get(r, 0.0))


def add_run(log: list[dict[str, Any]], run: dict[str, Any], now: float) -> list[dict[str, Any]]:
    """Append a finished run {ts, rooms, duration_s, area_m2}; drop old ones. Newest last."""
    cutoff = now - RUN_LOG_DAYS * 86400
    kept = [r for r in log if float(r.get("ts", 0)) >= cutoff]
    kept.append({"ts": float(run.get("ts", now)), "rooms": [int(x) for x in run.get("rooms") or []],
                 "duration_s": int(run.get("duration_s") or 0), "area_m2": _num(run.get("area_m2"))})
    return kept[-RUN_LOG_MAX:]


def summary(log: list[dict[str, Any]], now: float, days: int = 7) -> dict[str, Any]:
    """Totals for the last `days`: cleans, minutes, area, and how often each room was done."""
    cutoff = now - days * 86400
    recent = [r for r in log if float(r.get("ts", 0)) >= cutoff]
    per_room: dict[int, int] = {}
    for run in recent:
        for room in run.get("rooms") or []:
            per_room[int(room)] = per_room.get(int(room), 0) + 1
    return {"cleans": len(recent), "minutes": round(sum(int(r.get("duration_s") or 0) for r in recent) / 60),
            "area_m2": round(sum(_num(r.get("area_m2")) or 0 for r in recent), 1), "per_room": per_room}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
