import base64, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "eufy_x8_map"))
from lib.commands import CommandRecorder, decode_b64_json, history_entries, map_version, pick_dps  # noqa: E402


def b64(obj):
    return base64.b64encode(json.dumps(obj).encode()).decode()


def test_pick_dps_never_keeps_local_key():
    dev = {"localKey": "SECRET", "devId": "x", "dps": {"15": "Charging", "104": 100, "124": b64({"method": "goto"})}}
    out = pick_dps(dev)
    assert out.keys() == {"15", "124"} and "SECRET" not in json.dumps(out)


def test_recorder_decodes_and_dedupes():
    rec = CommandRecorder()
    goto = b64({"method": "goto", "data": {"target": "go", "x": 1, "y": 2}, "result": "O", "timestamp": 5})
    assert rec.add("124", goto, "t1", "state")
    assert not rec.add("124", goto, "t2", "state")
    assert rec.add("125", b64({"defaultID": 3, "version": 7}), "t3", "state")
    assert not rec.add("15", "Running", "t4", "state")
    assert rec.items[1]["method"] == "goto" and rec.items[1]["data"]["x"] == 1 and rec.items[0]["data"]["version"] == 7
    assert rec.add("124", "not-base64!!", "t5", "history") and rec.items[0]["raw"] == "not-base64!!"


def test_history_entries_any_shape():
    res = {"dps": [{"dpId": 124, "value": "abc", "timeStamp": 1}], "page": {"list": [{"dpid": "125", "value": "x", "time": 2}]}}
    assert {(e["dp"], e["t"]) for e in history_entries(res)} == {("124", 1), ("125", 2)}


def test_map_version():
    assert map_version({"125": b64({"defaultID": 3, "version": 9})}) == (3, 9)
    assert map_version({}) is None and decode_b64_json("short") is None


def test_safe_list():
    import pytest
    from lib.commands import CommandRefused, check_method, encode_command
    for ok in ["clear", "stop", "roomClean", "selectRoomsClean", "zoneClean", "spotClean"]:
        assert check_method(ok) == ok
    for bad in ["divideRoom", "mergeRooms", "renameRoom", "resetMap", "deleteMap", "saveMap", "otaUpdate",
                "factoryReset", "setWifi", "", "drop table", "cleanMap", "goto"]:
        with pytest.raises(CommandRefused):
            check_method(bad)
    value = encode_command("roomClean", {"ids": [3]}, now_ms=5)
    assert json.loads(base64.b64decode(value)) == {"method": "roomClean", "data": {"ids": [3]}, "timestamp": 5}


def test_pixel_pose_round_trip():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_integration_core import layout
    from lib.engine import MapState
    st = MapState(); st.update_layout(layout(origin=(-2320, -770)), 0)
    assert st.to_pose((232.0, 77.0)) == (0, 0)
    assert st.to_pixel(st.to_pose((120.9, 130.6))) == (120.9, 130.6)
