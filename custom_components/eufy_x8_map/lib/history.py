"""Run tracking for the Eufy X8 (ha-eufy-x8-map, Apache-2.0). HA-free.

Turns the robot's status (DPS 15) plus the room under it into events:
clean_started, room_entered, room_cleaned, clean_finished, error.
A room counts as cleaned once the robot has spent MIN_DWELL_S in it during a run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RUNNING = {"Running", "Locating"}
FINISHED = {"Charging", "Sleeping", "completed"}  # "standby" mid-run = paused, not finished
ERROR_STATUSES = {"recharge_needed"}
MIN_DWELL_S = 30.0


@dataclass
class RunTracker:
    running: bool = False
    started: float | None = None
    room: int | None = None
    room_since: float | None = None
    dwell: dict[int, float] = field(default_factory=dict)
    cleaned: list[int] = field(default_factory=list)   # rooms counted this run, in order
    last_cleaned: dict[int, float] = field(default_factory=dict)  # room -> wall-clock time (epoch s)
    last_error: Any = None

    def _leave_room(self, now: float, wall: float, events: list[dict[str, Any]]) -> None:
        if self.room is None or self.room_since is None:
            return
        total = self.dwell.get(self.room, 0.0) + (now - self.room_since)
        self.dwell[self.room] = total
        if total >= MIN_DWELL_S and self.room not in self.cleaned:
            self.cleaned.append(self.room)
            self.last_cleaned[self.room] = wall
            events.append({"type": "room_cleaned", "room": self.room})
        self.room_since = now

    def update(self, status: Any, room: int | None, error: Any, now: float, wall: float) -> list[dict[str, Any]]:
        """Feed the latest state. `now` is monotonic seconds, `wall` is epoch seconds. Returns new events."""
        events: list[dict[str, Any]] = []
        err = error if error not in (None, 0, "0", "", "no_error") else None
        if status in ERROR_STATUSES and err is None:
            err = status
        if err != self.last_error:
            self.last_error = err
            if err is not None:
                events.append({"type": "error", "code": err})
        if status in RUNNING and not self.running:
            self.running, self.started = True, now
            self.room, self.room_since, self.dwell, self.cleaned = None, None, {}, []
            events.append({"type": "clean_started"})
        if self.running:
            if room != self.room and status == "Running":
                self._leave_room(now, wall, events)
                self.room, self.room_since = room, now
                if room is not None:
                    events.append({"type": "room_entered", "room": room})
            elif self.room is not None:
                if status == "Running":
                    self._leave_room(now, wall, events)  # keep dwell up to date
                else:
                    self.room_since = now  # locating/returning time doesn't count as cleaning
            if status in FINISHED:
                events.append({"type": "clean_finished", "rooms": list(self.cleaned),
                                "duration_s": round(now - (self.started or now))})
                self.running, self.room, self.room_since = False, None, None
        return events
