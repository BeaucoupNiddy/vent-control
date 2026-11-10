"""'Update now' button: API endpoints and deploy/update.sh status reporting."""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from ventctl import updater

from .test_api import client

REAL_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "update.sh"
needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd)})


def _fake_install(tmp_path, script_body):
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    (repo / "deploy" / "update.sh").write_text(script_body)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", 'First "version"')
    return repo


def test_update_unavailable_without_git_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("VENTCTL_REPO", str(tmp_path))
    c = client(tmp_path)
    r = c.get("/api/update").json()
    assert r["available"] is False and r["reason"]
    assert c.post("/api/update").status_code == 409


@needs_git
def test_update_now_runs_script_and_reports_status(tmp_path, monkeypatch):
    repo = _fake_install(tmp_path, '#!/bin/bash\nmkdir -p data\n'
                         'echo \'{"state": "up_to_date", "time": "2026-01-01T00:00:00+00:00", "message": "Already up to date."}\' '
                         '> data/update-status.json\necho ran >> data/update.log\n')
    monkeypatch.setenv("VENTCTL_REPO", str(repo))
    monkeypatch.setattr(updater, "_launch_agent_loaded", lambda: False)
    c = client(tmp_path)
    info = c.get("/api/update").json()
    assert info["available"] and info["version"]["subject"] == 'First "version"'
    assert c.post("/api/update").json()["started"]
    for _ in range(50):
        st = c.get("/api/update").json()
        if st["status"] and st["status"]["state"] != "running":
            break
        time.sleep(0.1)
    assert st["status"]["state"] == "up_to_date"
    assert st["log"][-1] == "ran"


def test_second_update_refused_while_running(tmp_path, monkeypatch):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "update.sh").write_text("")
    (tmp_path / ".git").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "update-status.json").write_text(json.dumps(
        {"state": "running", "time": updater.datetime.now(updater.timezone.utc).isoformat(), "message": "…"}))
    monkeypatch.setenv("VENTCTL_REPO", str(tmp_path))
    r = client(tmp_path).post("/api/update")
    assert r.status_code == 409 and "already" in r.json()["detail"]
    # an old "running" status is not trusted forever
    (tmp_path / "data" / "update-status.json").write_text(json.dumps(
        {"state": "running", "time": "2020-01-01T00:00:00+00:00", "message": "…"}))
    assert updater.info()["status"]["state"] == "unknown"


@needs_git
def test_real_update_script_writes_status_when_up_to_date(tmp_path):
    origin = _fake_install(tmp_path, REAL_SCRIPT.read_text())
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    subprocess.run(["/bin/bash", str(clone / "deploy" / "update.sh")], cwd=clone, check=True, timeout=30)
    st = json.loads((clone / "data" / "update-status.json").read_text())
    assert st["state"] == "up_to_date"
    assert not (clone / "data" / "update.lock").exists()
