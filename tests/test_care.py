import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "eufy_x8_map"))
from lib.care import add_run, consumables, rooms_due, summary  # noqa: E402

LIVE_116 = '{"consumable":{"duration":{"SB":81,"RB":48,"FM":48,"SP":81,"SS":15,"TR":246,"BatteryStatus":1}}}'


def test_consumables_match_the_app():
    # App, 21 Nov 2026: sensors 20/35 h 57 %, side brush 169/250 h 67 %, filter 152/200 h 76 %, rolling 402/450 h 89 %
    c = consumables(LIVE_116)
    assert {k: (v["left_h"], v["percent"]) for k, v in c.items()} == {
        "sensors": (20, 57), "side_brush": (169, 67), "filter": (152, 76), "rolling_brush": (402, 89)}
    assert consumables({"consumable": {"duration": {"SB": 300}}})["side_brush"]["percent"] == 0
    assert consumables("not json") == {} and consumables(None) == {}


def test_rooms_due():
    day = 86400
    now = 100 * day
    last = {1: now - 1 * day, 2: now - 8 * day, 4: now - 20 * day}
    assert rooms_due([1, 2, 4, 6], last, 7, now) == [6, 4, 2]      # never cleaned first, then oldest
    assert rooms_due([1, 2], last, 30, now) == []


def test_run_log_and_summary():
    day = 86400
    now = 100 * day
    log: list = []
    log = add_run(log, {"ts": now - 70 * day, "rooms": [1], "duration_s": 600, "area_m2": 5}, now - 70 * day)
    log = add_run(log, {"ts": now - 2 * day, "rooms": [2, 7], "duration_s": 1200, "area_m2": "7"}, now)
    log = add_run(log, {"ts": now - 9 * day, "rooms": [7], "duration_s": 300, "area_m2": None}, now)
    assert len(log) == 2                                              # 70-day-old run dropped
    s = summary(log, now)
    assert s == {"cleans": 1, "minutes": 20, "area_m2": 7.0, "per_room": {2: 1, 7: 1}}
    assert summary(log, now, days=14)["per_room"] == {2: 1, 7: 2}


def test_care_report_from_log_shapes():
    from lib.care import consumable_hours
    from lib.commands import history_entries
    log = {"datas": [{"dpId": 116, "value": LIVE_116, "timeStamp": 1795200000},
                     {"dpId": 104, "value": 88, "timeStamp": 1795200001}]}
    hits = [e for e in history_entries(log) if e["dp"] == "116" and consumable_hours(e["value"])]
    assert len(hits) == 1 and consumable_hours(hits[0]["value"])["SB"] == 81
