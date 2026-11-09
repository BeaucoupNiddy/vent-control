from fastapi.testclient import TestClient

from ventctl.app import create_app


def client(tmp_path, hours=3):
    return TestClient(create_app(str(tmp_path / "t.db"), backfill_hours=hours, start_loop=False))


def test_api_smoke(tmp_path):
    with client(tmp_path) as c:
        s = c.get("/api/status").json()
        assert s["data_source"] == "mock" and s["groups"][0]["decision"]
        assert len(c.get("/api/history?range=24h").json()["rows"]) > 10
        assert "fan_runtime_h" in c.get("/api/stats").json()
        assert c.get("/api/events").json()
        assert len(c.get("/api/sim/profiles").json()) >= 5
        r = c.post("/api/sim/run", json={"profile_id": "nola_summer", "params": {"duration_h": 24}}).json()
        assert len(r["results"]) == 10 and r["kind"] == "modeled_simulation"
        assert c.post("/api/replay", json={"range": "24h"}).json()["kind"] == "opportunity_replay"
        calc = c.post("/api/calc", json={"t_f": 79, "rh_pct": 95}).json()
        assert abs(calc["dew_point_f"] - 77.4) < 0.2


def test_config_validation_and_persistence(tmp_path):
    with client(tmp_path, 0) as c:
        cfg = c.get("/api/config").json()
        cfg["control"]["dewpoint_off_advantage_f"] = 5  # > on-advantage: invalid
        assert c.put("/api/config", json=cfg).status_code == 422
        cfg["control"]["dewpoint_off_advantage_f"] = 1.5
        assert c.put("/api/config", json=cfg).status_code == 200
    with client(tmp_path, 0) as c:
        assert c.get("/api/config").json()["control"]["dewpoint_off_advantage_f"] == 1.5


def test_mode_and_fault_endpoints(tmp_path):
    with client(tmp_path, 0) as c:
        s = c.post("/api/mode", json={"mode": "manual_on", "minutes": 10}).json()
        assert s["mode"] == "manual_on" and s["groups"][0]["manual_until"]
        st = c.post("/api/mock/fault", json={"target": "switch", "id": "mock-plug-1", "fault": "unreachable"}).json()
        assert st["switch_faults"]["mock-plug-1"] == "unreachable"
        assert c.post("/api/mode", json={"mode": "auto"}).json()["mode"] == "auto"
