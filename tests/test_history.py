import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "eufy_x8_map"))
from lib.history import RunTracker  # noqa: E402


def run(tr, steps):
    out = []
    for t, status, room, err in steps:
        out += [e["type"] + (f":{e.get('room')}" if "room" in e else "") for e in tr.update(status, room, err, t, 1000 + t)]
    return out


def test_run_rooms_and_finish():
    tr = RunTracker()
    ev = run(tr, [(0, "Charging", None, 0), (10, "Locating", None, 0), (40, "Running", 2, 0), (60, "Running", 2, 0),
                  (80, "Running", 7, 0), (90, "Running", 2, 0), (100, "Recharge", 4, 0), (130, "Charging", None, 0)])
    assert ev[0] == "clean_started"
    assert "room_cleaned:2" in ev and "room_cleaned:7" not in ev  # kitchen only 10 s
    assert ev[-1] == "clean_finished"
    assert tr.cleaned == [2] and tr.last_cleaned[2] == 1080 and not tr.running


def test_error_once():
    tr = RunTracker()
    assert run(tr, [(0, "Running", 1, 0), (5, "Running", 1, 3), (6, "Running", 1, 3)]).count("error") == 1
    assert run(tr, [(7, "Running", 1, 0), (8, "recharge_needed", 1, 0)]).count("error") == 1
