import json
import os
import stat

from fastapi.testclient import TestClient

from ventctl import explainer
from ventctl.app import create_app


def client(tmp_path, hours=3):
    return TestClient(create_app(str(tmp_path / "t.db"), backfill_hours=hours, start_loop=False))


def test_key_is_stored_privately_and_never_returned(tmp_path):
    with client(tmp_path, 0) as c:
        assert c.get("/api/chat/settings").json()["has_key"] is False
        r = c.put("/api/chat/settings", json={"api_key": "sk-or-v1-secretsecret1234", "model": "x/y"}).json()
        assert r == {"has_key": True, "key_hint": "…1234", "model": "x/y"}
        f = tmp_path / "openrouter.json"
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
        # the key is not in config, the export, events or the context the model sees
        for path in ("/api/config", "/api/export/config", "/api/events", "/api/chat/context", "/api/status"):
            assert "secretsecret" not in c.get(path).text
        assert c.put("/api/chat/settings", json={"api_key": ""}).json()["has_key"] is False


def test_chat_sends_rules_and_live_context(tmp_path, monkeypatch):
    seen = {}

    def fake_http(url, key, body, timeout):
        seen.update(url=url, key=key, body=body)
        return {"model": body["model"], "choices": [{"message": {"content": " The fans are off because... "}}]}

    monkeypatch.setattr(explainer, "_http", fake_http)
    with client(tmp_path) as c:
        assert c.post("/api/chat", json={"messages": [{"role": "user", "content": "why?"}]}).status_code == 400
        c.put("/api/chat/settings", json={"api_key": "sk-or-v1-abcdefgh"})
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "Why are the fans off?"}],
                                      "model": "test/model"}).json()
        assert r["reply"] == "The fans are off because..." and r["model"] == "test/model"
        assert seen["url"].endswith("/chat/completions") and seen["key"] == "sk-or-v1-abcdefgh"
        sys_msg = seen["body"]["messages"][0]
        assert sys_msg["role"] == "system"
        assert "humidity ratio" in sys_msg["content"] and "reason_code" in sys_msg["content"]
        assert "Recent events" in sys_msg["content"] and "Recent trend" in sys_msg["content"]
        assert seen["body"]["messages"][-1] == {"role": "user", "content": "Why are the fans off?"}
        assert c.get("/api/chat/settings").json()["model"] == "test/model"  # last model remembered


def test_openrouter_errors_are_friendly(tmp_path, monkeypatch):
    def boom(*a):
        raise explainer.OpenRouterError("OpenRouter didn't accept the API key. Check it and paste it again.")

    monkeypatch.setattr(explainer, "_http", boom)
    with client(tmp_path, 0) as c:
        c.put("/api/chat/settings", json={"api_key": "sk-or-v1-abcdefgh"})
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 502 and "API key" in r.json()["detail"]


def test_models_list_parsed(monkeypatch):
    explainer._models_cache = None
    monkeypatch.setattr(explainer, "_http", lambda *a: {"data": [
        {"id": "b/two", "name": "Two", "pricing": {"prompt": "0.000003", "completion": "0.000015"},
         "context_length": 200000},
        {"id": "a/img", "name": "Image only", "architecture": {"output_modalities": ["image"]}},
        {"id": "a/one", "name": "One", "pricing": {"prompt": "0", "completion": "0"}},
    ]})
    import asyncio
    ms = asyncio.run(explainer.list_models())
    assert [m["id"] for m in ms] == ["a/one", "b/two"]
    assert ms[1]["prompt_per_m"] == 3.0 and ms[1]["completion_per_m"] == 15.0
    explainer._models_cache = None


def test_redact_hides_credentials():
    cfg = {"switches": {"tapo_password": "pw", "tapo_username": "me@x", "shelly_devices": [{"password": "p", "host": "h"}]},
           "sensors": {"homeassistant": {"token": "t"}}, "control": {"target_rh_pct": 60}}
    out = json.dumps(explainer.redact(cfg))
    assert "pw" not in out and "me@x" not in out and '"t"' not in out and '"p"' not in out
    assert '"h"' in out and "60" in out
