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


def test_v3_zone_surface_circulation_endpoints(tmp_path):
    with client(tmp_path, 3) as c:
        z = c.get("/api/zones").json()
        assert set(z["zones"]) == {"main", "pocket", "damp"}
        assert z["summary"]["wettest_w"] and z["connections"]
        assert all("dw_g_kg" in g for g in z["connections"])
        sf = c.get("/api/surfaces").json()["surfaces"]
        assert sf and sf[0]["zone"] == "damp" and sf[0]["risk"]
        circ = c.get("/api/circulation").json()
        assert circ["mode"] == "shadow" and len(circ["circulators"]) == 2
        # automation cannot be enabled before commissioning
        assert c.post("/api/circulation/mode", json={"mode": "auto"}).status_code == 400
        r = c.post("/api/circulators/circ-1/manual", json={"state": "on", "minutes": 15}).json()
        assert next(x for x in r["circulators"] if x["id"] == "circ-1")["manual"] is True
        assert c.post("/api/circulators/nope/manual", json={"state": "on"}).status_code == 404
        assert c.post("/api/circulators/circ-1/manual", json={"state": "maybe"}).status_code == 400
        st = c.get("/api/zones/stats?range=24h").json()
        assert "pocket" in st["zones"] and st["surfaces"]
        eff = c.get("/api/circulation/effectiveness").json()
        assert len(eff["fans"]) == 2 and "Informational" in eff["label"]
        assert c.get("/api/circulation/cycles").status_code == 200
        cmp_ = c.post("/api/sensors/compare", json={"action": "start"}).json()
        assert cmp_["active"] and len(cmp_["sensor_ids"]) == 3
        assert c.get("/api/sensors/compare").json()["active"]
        assert c.post("/api/sensors/compare", json={"action": "stop"}).json()["applied"] == []
        sim = c.post("/api/sim/zones", json={"params": {"duration_h": 12}, "strategies": ["none", "both"]}).json()
        assert [r["id"] for r in sim["results"]] == ["none", "both"]
        assert len(c.get("/api/sim/zones/strategies").json()) == 8
