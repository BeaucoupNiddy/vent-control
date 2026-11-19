"""'Restart app' support: restart the controller from the dashboard.

The app stops itself and lets its supervisor start it again: the com.ventctl LaunchAgent (KeepAlive) on
the Mac, or Docker's restart policy. On startup the controller reads every plug, so a restart clears
leftovers such as a command lockout after a network outage. Without a supervisor a stop would not come
back, so the button is unavailable.
"""

from __future__ import annotations

import os
import signal
import threading
from pathlib import Path

SERVICE = "com.ventctl"
DELAY_S = 1.0  # long enough for the HTTP response to reach the browser


def supervisor() -> str | None:
    if os.environ.get("XPC_SERVICE_NAME") == SERVICE:
        return "launchd"
    if Path("/.dockerenv").exists():
        return "docker"
    return None


def info() -> dict:
    sup = supervisor()
    return {
        "available": sup is not None,
        "supervisor": sup,
        "reason": None if sup else "Restarting from the dashboard needs the app installed with deploy/install-mac.sh "
                                   "(or running in Docker). Otherwise restart it where you started it.",
    }


def _stop() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def schedule(stop=_stop, delay_s: float = DELAY_S) -> dict:
    """Stop the app shortly, so the supervisor starts it again. Raises RuntimeError if nothing would."""
    sup = supervisor()
    if sup is None:
        raise RuntimeError(info()["reason"])
    t = threading.Timer(delay_s, stop)
    t.daemon = True
    t.start()
    return {"restarting": True, "supervisor": sup}
