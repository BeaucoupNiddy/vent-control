from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from ventctl.app import create_app


def client(tmp_path, hours=3):
    return TestClient(create_app(str(tmp_path / "t.db"), backfill_hours=hours, start_loop=False))


def run_tick(c):
    import asyncio
    asyncio.run(c.app.state.vc["ctrl"].tick())


def test_dismiss_hides_until_cleared_and_restore(tmp_path):
    with client(tmp_path, 0) as c:
        ctrl = c.app.state.vc["ctrl"]
        ctrl._set_alert(ctrl.clock.now(), "PIPES_UNMONITORED", True, "warning", "test alert")
        s = c.post("/api/alerts/dismiss", json={"ids": ["PIPES_UNMONITORED"]}).json()
        assert all(a["id"] != "PIPES_UNMONITORED" for a in s["alerts"]) and s["dismissed_alerts"] >= 1
        s = c.post("/api/alerts/dismiss", json={"restore": True}).json()
        assert s["dismissed_alerts"] == 0
        c.post("/api/alerts/dismiss", json={})  # dismiss all
        assert c.get("/api/status").json()["alerts"] == []
        saved = dict(ctrl.storage.get_kv("dismissed_alerts"))
        assert "PIPES_UNMONITORED" in saved
    with client(tmp_path, 0) as c:  # dismissals survive a restart
        assert c.app.state.vc["ctrl"].dismissed_alerts == saved


def test_alert_dismissal_resets_when_alert_clears(tmp_path):
    with client(tmp_path, 0) as c:
        ctrl = c.app.state.vc["ctrl"]
        now = ctrl.clock.now()
        ctrl._set_alert(now, "EXCESSIVE_SWITCHING", True, "warning", "x")
        ctrl.dismiss_alerts(["EXCESSIVE_SWITCHING"])
        ctrl._set_alert(now, "EXCESSIVE_SWITCHING", False)
        assert "EXCESSIVE_SWITCHING" not in ctrl.dismissed_alerts
        ctrl._set_alert(now, "EXCESSIVE_SWITCHING", True, "warning", "x")
        assert any(a["id"] == "EXCESSIVE_SWITCHING" for a in ctrl.status()["alerts"])


def test_stale_alerts_for_removed_things_are_cleared(tmp_path):
    with client(tmp_path, 0) as c:
        ctrl = c.app.state.vc["ctrl"]
        ctrl._set_alert(ctrl.clock.now(), "SURFACE_WARNING:old-probe-that-was-removed", True, "warning", "old")
        ctrl._set_alert(ctrl.clock.now(), "MOTOR_LOAD:deleted-fan", True, "warning", "old")
        run_tick(c)
        ids = {a["id"] for a in c.get("/api/status").json()["alerts"]}
        assert "SURFACE_WARNING:old-probe-that-was-removed" not in ids and "MOTOR_LOAD:deleted-fan" not in ids


def test_reset_data_since_before_all(tmp_path):
    with client(tmp_path, 6) as c:
        st = c.app.state.vc["storage"]
        now = c.app.state.vc["ctrl"].clock.now()
        total = st.history_count()
        assert total > 20
        cut = (now - timedelta(hours=2)).astimezone(timezone.utc)
        r = c.post("/api/data/reset", json={"scope": "since", "at": cut.isoformat()}).json()
        assert r["deleted"]["history"] > 0 and (tmp_path / "backups").exists()
        assert st.latest_history_ts() < cut
        left = st.history_count()
        r = c.post("/api/data/reset", json={"scope": "before", "at": (now - timedelta(hours=4)).isoformat()}).json()
        assert 0 < st.history_count() < left
        assert c.post("/api/data/reset", json={"scope": "since"}).status_code == 400
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        assert c.post("/api/data/reset", json={"scope": "before", "at": future}).status_code == 400
        assert c.post("/api/data/reset", json={"scope": "nope"}).status_code == 400
        c.post("/api/data/reset", json={"scope": "all"})
        assert st.history_count() == 0
        ev = c.get("/api/events").json()
        assert len(ev) == 1 and ev[0]["reason_code"] == "DATA_RESET"  # only the record of the reset remains
        assert c.get("/api/config").json()  # settings kept
