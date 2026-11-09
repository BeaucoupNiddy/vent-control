"""Guided setup walkthrough: checks and saved progress."""

from fastapi.testclient import TestClient

from ventctl import setup_guide
from ventctl.app import create_app
from ventctl.config import SensorMapping, SensorRole, TapoDevice, example_config


def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "t.db"), backfill_hours=0, start_loop=False))


def checks(result, step):
    s = next(x for x in result["steps"] if x["id"] == step)
    return s, {c["id"]: c for c in s["checks"]}


def test_progress_saves_and_resumes(tmp_path):
    with client(tmp_path) as c:
        r = c.get("/api/setup").json()
        assert r["total"] == len(setup_guide.STEPS) and r["done"] == 0
        assert r["progress"]["current_step"] == "prepare"
        r = c.put("/api/setup/progress", json={"answers": {"worksheet_ready": True}, "current_step": "gateway",
                                               "paused": True, "mark": {"step": "prepare", "state": "done"}}).json()
        assert r["done"] == 1 and r["progress"]["paused"]
    with client(tmp_path) as c:  # restart: progress came back from the database
        r = c.get("/api/setup").json()
        assert r["progress"]["current_step"] == "gateway" and r["progress"]["answers"]["worksheet_ready"]
        _, ch = checks(r, "prepare")
        assert ch["worksheet_ready"]["status"] == "ok" and ch["mac_stays_on"]["status"] == "todo"
        r = c.put("/api/setup/progress", json={"mark": {"step": "prepare", "state": None}}).json()
        assert r["done"] == 0
        r = c.post("/api/setup/reset").json()
        assert r["progress"]["answers"] == {} and r["progress"]["current_step"] == "prepare"


def test_mock_hardware_is_flagged(tmp_path):
    with client(tmp_path) as c:
        r = c.get("/api/setup").json()
        s, ch = checks(r, "gateway")
        assert not s["ready"] and ch["provider"]["status"] == "fail"
        _, ch = checks(r, "plugs")
        assert ch["provider"]["status"] == "fail"
        _, ch = checks(r, "golive")
        assert ch["mock"]["status"] == "fail"


def test_sensor_and_plug_checks():
    cfg = example_config()
    cfg.sensors.provider = "ecowitt"
    cfg.sensors.ecowitt_host = "192.168.0.50"
    cfg.sensors.mappings = [
        SensorMapping(sensor_id="ecowitt:outdoor", name="Outdoor", role=SensorRole.OUTDOOR),
        SensorMapping(sensor_id="ecowitt:ch1", name="Main", role=SensorRole.BASEMENT, zone="main"),
    ]
    cfg.switches.provider = "tapo"
    cfg.switches.tapo_devices = [TapoDevice(switch_id="tapo-1", host="192.168.0.60"),
                                 TapoDevice(switch_id="tapo-2", host="192.168.0.60")]
    status = {"sensors": [{"sensor_id": "ecowitt:outdoor", "name": "Outdoor", "status": "ok"},
                          {"sensor_id": "ecowitt:ch1", "name": "Main", "status": "stale"}],
              "hardware": {}, "alerts": []}
    ctx = {"channels": [{"id": "ecowitt:outdoor"}, {"id": "ecowitt:ch1"}, {"id": "ecowitt:ch2"}],
           "plug_probe": {"tapo-1": {"reachable": True}}}
    r = setup_guide.evaluate(cfg, status, setup_guide.empty_progress(), ctx)
    _, ch = checks(r, "sensors")
    assert ch["outdoor"]["status"] == "ok"
    assert ch["zone-main"]["status"] == "fail"  # stale reading
    assert ch["zone-pocket"]["status"] == "fail"  # no sensor
    assert ch["probe"]["status"] == "fail"  # no concrete probe and no estimate
    assert "ecowitt:ch2" in ch["unmapped"]["label"]
    _, ch = checks(r, "plugs")
    assert ch["creds"]["status"] == "fail" and ch["dupe"]["status"] == "fail"
    assert any(k.startswith("assign-") and v["status"] == "fail" for k, v in ch.items())  # still mock-plug ids
    _, ch = checks(r, "gateway")
    assert ch["reach"]["status"] == "ok"


def test_gas_appliance_needs_interlock():
    cfg = example_config()
    p = setup_guide.empty_progress()
    p["answers"] = {"gas_appliance": "natural"}
    _, ch = checks(setup_guide.evaluate(cfg, {"hardware": {}}, p), "safety")
    assert ch["gas_appliance"]["status"] == "fail"
    cfg.control.combustion_interlock = True
    _, ch = checks(setup_guide.evaluate(cfg, {"hardware": {}}, p), "safety")
    assert ch["gas_appliance"]["status"] == "warn"
