"""'Update now' support: report the installed version and start deploy/update.sh on request.

The update itself is done by deploy/update.sh (the same script the 30-minute LaunchAgent runs).
It writes data/update-status.json, which this module reads back for the dashboard.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

UPDATE_AGENT = "com.ventctl.update"
STALE_RUNNING_S = 20 * 60  # a "running" status older than this is treated as finished
_EXTRA_PATH = "/opt/homebrew/bin:/usr/local/bin"


def repo_root() -> Path:
    return Path(os.environ.get("VENTCTL_REPO") or Path(__file__).resolve().parents[2])


def _paths(root: Path) -> dict[str, Path]:
    return {
        "script": root / "deploy" / "update.sh",
        "status": root / "data" / "update-status.json",
        "log": root / "data" / "update.log",
    }


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{_EXTRA_PATH}:{env.get('PATH', '/usr/bin:/bin')}"
    return env


def _git(root: Path, *args: str) -> str | None:
    git = shutil.which("git", path=_env()["PATH"])
    if not git:
        return None
    try:
        r = subprocess.run([git, *args], cwd=root, env=_env(), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _read_status(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if data.get("state") == "running":
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["time"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            age = STALE_RUNNING_S + 1
        if age > STALE_RUNNING_S:
            data["state"] = "unknown"
            data["message"] = "The last update did not report back. Check data/update.log."
    return data


def _log_tail(path: Path, lines: int = 12) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def info() -> dict:
    root = repo_root()
    p = _paths(root)
    available = p["script"].exists() and (root / ".git").exists()
    version = None
    if available:
        out = _git(root, "log", "-1", "--format=%h%x1f%s%x1f%cI")
        if out:
            commit, subject, date = (out.split("\x1f") + ["", "", ""])[:3]
            version = {"commit": commit, "subject": subject, "date": date}
    return {
        "available": available,
        "reason": None if available else "Automatic updates need the app installed from GitHub with deploy/install-mac.sh.",
        "version": version,
        "status": _read_status(p["status"]),
        "log": _log_tail(p["log"]),
    }


def _launch_agent_loaded() -> bool:
    if sys.platform != "darwin" or not shutil.which("launchctl"):
        return False
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{UPDATE_AGENT}"], capture_output=True, timeout=5)
    return r.returncode == 0


def start() -> dict:
    """Start an update in the background. Raises RuntimeError with a plain message if it can't."""
    root = repo_root()
    p = _paths(root)
    if not (p["script"].exists() and (root / ".git").exists()):
        raise RuntimeError("Automatic updates need the app installed from GitHub with deploy/install-mac.sh.")
    st = _read_status(p["status"])
    if st and st.get("state") == "running":
        raise RuntimeError("An update is already running.")
    p["status"].parent.mkdir(parents=True, exist_ok=True)
    # Mark it as starting right away so the dashboard shows progress before the script writes.
    p["status"].write_text(json.dumps({
        "state": "running", "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message": "Checking GitHub for a new version…"}))
    if _launch_agent_loaded():
        # Run it as the update LaunchAgent so it survives the app restarting at the end.
        subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{UPDATE_AGENT}"], capture_output=True, timeout=10)
        how = "agent"
    else:
        with open(p["log"], "a") as log:
            subprocess.Popen(["/bin/bash", str(p["script"])], cwd=root, env=_env(), stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        how = "script"
    return {"started": True, "how": how}
