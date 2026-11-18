"""'Restart app' button: only offered under a supervisor that brings the app back."""

import threading

from .test_api import client


def test_restart_unavailable_without_supervisor(tmp_path, monkeypatch):
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    monkeypatch.setattr("ventctl.restart.Path.exists", lambda self: False)
    c = client(tmp_path)
    r = c.get("/api/restart").json()
    assert r["available"] is False and r["reason"]
    assert c.post("/api/restart").status_code == 409


def test_restart_under_launchd_stops_the_app_and_logs_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.ventctl")
    with client(tmp_path) as c:
        assert c.get("/api/restart").json() == {"available": True, "supervisor": "launchd", "reason": None}
        stopped = threading.Event()
        c.app.state.restart_stop = stopped.set
        r = c.post("/api/restart")
        assert r.status_code == 200 and r.json()["restarting"] is True
        assert stopped.wait(5)
        events = c.get("/api/events").json()
    events = events.get("events", events) if isinstance(events, dict) else events
    assert any(e.get("reason_code") == "APP_RESTART" or "restarted from the dashboard" in (e.get("message") or "")
               for e in events)
