from __future__ import annotations

from datetime import datetime, timezone


class Clock:
    """Wall clock. Subclasses let tests and demos control time."""

    speed: float = 1.0

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ManualClock(Clock):
    def __init__(self, t: datetime):
        self.t = t

    def now(self) -> datetime:
        return self.t

    def set(self, t: datetime) -> None:
        self.t = t


class AcceleratedClock(Clock):
    """Runs `speed` times faster than real time from an anchor (demo mode)."""

    def __init__(self, start: datetime, speed: float):
        self.anchor_virtual = start
        self.anchor_real = datetime.now(timezone.utc)
        self.speed = speed

    def now(self) -> datetime:
        real = datetime.now(timezone.utc)
        return self.anchor_virtual + (real - self.anchor_real) * self.speed
