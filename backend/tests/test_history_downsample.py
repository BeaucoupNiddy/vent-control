"""Thinned history (7-day charts) must still show readings that resumed after a sensor outage."""

from datetime import datetime, timedelta, timezone

from ventctl.storage import Storage, _downsample


def _row(i, ok):
    return {"i": i, "indoor_t_c": 20.0 if ok else None, "outdoor_t_c": 18.0 if ok else None}


def test_downsample_keeps_newest_row_and_outage_edges():
    # 900 good rows, a 100-row outage, then 12 rows after the network came back
    rows = [_row(i, True) for i in range(900)] + [_row(i, False) for i in range(900, 1000)] + \
           [_row(i, True) for i in range(1000, 1012)]
    out = _downsample(rows, 200)
    idx = [r["i"] for r in out]
    assert idx == sorted(set(idx))
    assert idx[-1] == 1011                       # the newest reading is always there
    assert {899, 900, 999, 1000} <= set(idx)     # both edges of the outage
    after = [i for i in idx if i >= 1000]
    assert len(after) >= 2                       # enough to draw a line after the outage
    assert len(out) <= 200 + 5


def test_downsample_ignores_edges_when_a_sensor_flaps():
    rows = [_row(i, i % 2 == 0) for i in range(1000)]
    out = _downsample(rows, 100)
    assert len(out) <= 101 and out[-1]["i"] == 999


def test_storage_history_returns_resumed_readings(tmp_path):
    s = Storage(str(tmp_path / "h.db"))
    t0 = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    for i in range(300):
        ok = not (200 <= i < 280)
        s.add_history(t0 + timedelta(minutes=2 * i), {"source": "measured", "fans_on": 0, "fans_verified": 1,
                                                       "indoor_t_c": 20.0 if ok else None,
                                                       "outdoor_t_c": 18.0 if ok else None})
    rows = s.history(t0, t0 + timedelta(hours=12), max_points=50)
    assert rows[-1]["ts"] == (t0 + timedelta(minutes=2 * 299)).isoformat()
    assert sum(1 for r in rows if r["ts"] >= (t0 + timedelta(minutes=560)).isoformat() and r["indoor_t_c"]) >= 2
