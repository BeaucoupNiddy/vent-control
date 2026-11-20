"""Forecast calibrated to the outdoor sensor: joins the live reading at now, fades to the typical offset."""

import asyncio
from datetime import datetime, timedelta, timezone

from ventctl.forecast import BIAS_DECAY_H, ForecastCache, parse_open_meteo
from ventctl.psychro import AirState, rh_from_dew_point

NOW = datetime(2026, 11, 20, 22, 0, tzinfo=timezone.utc)


def air(t, td):
    return AirState.from_t_rh(t, rh_from_dew_point(t, td))


class Fixed:
    source = "test"

    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, lat, lon, now):
        return self.rows


class Cfg:
    class temperature:
        forecast_enabled = True
        forecast_latitude = 29.95
        forecast_longitude = -90.07


def model_rows(hours_back=72, hours_ahead=24, t=20.0, td=14.0):
    return [(NOW + timedelta(hours=h), air(t, td)) for h in range(-hours_back, hours_ahead + 1)]


def cache_with(rows, obs):
    fc = ForecastCache(Fixed(rows))
    asyncio.run(fc.refresh(Cfg, NOW))
    for ts, t, td in obs:
        fc.observe(ts, air(t, td))
    return fc


def test_steady_offset_is_learned_and_applied():
    # sensor always 1.5 C warmer and 0.7 C more humid than the model
    obs = [(NOW - timedelta(minutes=10 * i), 21.5, 14.7) for i in range(48 * 6, -1, -1)]
    fc = cache_with(model_rows(), obs)
    b = fc.bias(NOW)
    assert b["hours"] >= 24
    assert abs(b["typical_t"] - 1.5) < 0.01 and abs(b["typical_td"] - 0.7) < 0.01
    cur = fc.current(NOW)
    assert all(abs(a.t_c - 21.5) < 0.01 and abs(a.dew_point_c - 14.7) < 0.05 for _, a in cur)
    st = fc.status(NOW, Cfg)
    assert st["calibration"]["typical_td_f"] == 1.3 and abs(st["hours"][0]["raw_dew_point_c"] - 14.0) < 1e-6


def test_forecast_starts_at_the_live_reading_then_fades_to_typical():
    obs = [(NOW - timedelta(minutes=10 * i), 20.0, 14.0) for i in range(48 * 6, 12, -1)]  # matched for 2 days
    obs += [(NOW - timedelta(minutes=10 * i), 20.0, 16.0) for i in range(12, -1, -1)]     # wetter right now
    fc = cache_with(model_rows(), obs)
    cur = fc.current(NOW)
    first = cur[0]  # one hour ahead
    expect = 2.0 * __import__("math").exp(-1 / BIAS_DECAY_H)
    assert abs((first[1].dew_point_c - 14.0) - expect) < 0.15
    late = cur[-1][1].dew_point_c - 14.0
    assert 0 <= late < 0.3  # a day out it is back to the typical (near zero) offset


def test_no_overlap_means_no_change_and_offsets_are_clamped():
    fc = cache_with(model_rows(hours_back=0), [])
    assert fc.bias(NOW) is None
    assert [a.dew_point_c for _, a in fc.current(NOW)] == [a.dew_point_c for _, a in model_rows(0)[1:]]
    # a broken sensor (40 C hotter) cannot drag the forecast more than the clamp
    obs = [(NOW - timedelta(minutes=10 * i), 60.0, 14.0) for i in range(24 * 6, -1, -1)]
    fc = cache_with(model_rows(), obs)
    assert max(a.t_c for _, a in fc.current(NOW)) <= 26.01


def test_dew_point_never_above_temperature_after_shift():
    obs = [(NOW - timedelta(minutes=10 * i), 15.0, 15.0) for i in range(24 * 6, -1, -1)]
    fc = cache_with(model_rows(t=20.0, td=13.0), obs)
    assert all(a.dew_point_c <= a.t_c + 1e-6 for _, a in fc.current(NOW))


def test_seed_from_history_rows_and_skip_mock():
    fc = ForecastCache(Fixed(model_rows()))
    asyncio.run(fc.refresh(Cfg, NOW))
    rows = [{"ts": (NOW - timedelta(minutes=10 * i)).isoformat(), "source": "measured",
             "outdoor_t_c": 21.0, "outdoor_td_c": 15.0} for i in range(24 * 6, -1, -1)]
    rows.append({"ts": NOW.isoformat(), "source": "mock", "outdoor_t_c": 99.0, "outdoor_td_c": 0.0})
    fc.seed(rows)
    assert fc.seeded and abs(fc.bias(NOW)["typical_td"] - 1.0) < 0.01


def test_parse_includes_past_hours():
    data = {"hourly": {"time": ["2026-11-19T00:00", "2026-11-21T00:00"], "temperature_2m": [10, 11],
                       "dew_point_2m": [5, 6]}}
    assert [ts.day for ts, _ in parse_open_meteo(data)] == [19, 21]
