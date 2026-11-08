"""Phase 4: NOAA weather parsing and cache, seasonal reports, annual estimates."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ventctl import reports, weather
from ventctl.app import create_app
from ventctl.config import example_config
from ventctl.simulation import SimParams
from ventctl.storage import Storage

# Shape of NOAA ADS global-hourly JSON (documented format; values are illustrative)
ADS_SAMPLE = [
    {"STATION": "72231012916", "DATE": "2025-07-01T00:53:00", "TMP": "+0289,5", "DEW": "+0239,5"},
    {"STATION": "72231012916", "DATE": "2025-07-01T00:59:00", "TMP": "+0291,5", "DEW": "+0241,5"},
    {"STATION": "72231012916", "DATE": "2025-07-01T01:53:00", "TMP": "+0283,5", "DEW": "+9999,9"},
    {"STATION": "72231012916", "DATE": "2025-07-01T02:53:00", "TMP": "+0278,5", "DEW": "+0240,5"},
    {"STATION": "72231012916", "DATE": "2025-07-01T03:53:00", "TMP": "+0500,7", "DEW": "+0238,1"},
    {"STATION": "72231012916", "DATE": "2025-07-01T05:53:00", "TMP": "+0270,5", "DEW": "+0236,5"},
]


def test_parse_ads_hourly_average_and_quality_flags():
    rows = weather.parse_ads(ADS_SAMPLE)
    by = {t.hour: (a, b) for t, a, b in rows}
    assert by[0] == (pytest.approx(29.0), pytest.approx(24.0))
    assert by[1] == (pytest.approx(28.3), None)  # missing dew point
    assert by[3][0] is None and by[3][1] == pytest.approx(23.8)  # suspect quality flag 7 dropped
    assert rows[0][0].tzinfo is not None


def test_cache_and_profile_with_gap_fill(tmp_path):
    s = Storage(tmp_path / "w.db")
    s.cache_weather(weather.SOURCE, "X", weather.parse_ads(ADS_SAMPLE))
    t0 = datetime(2025, 7, 1, tzinfo=timezone.utc)
    rows = s.cached_weather(weather.SOURCE, "X", t0, t0 + timedelta(days=1))
    assert len(rows) == 5
    prof = weather.profile_from_cache(rows, "X", t0, t0 + timedelta(days=1))
    assert prof.source == "observed" and prof.hours == 6  # hours 0,2,4(interp),5 ... interpolated to hourly
    assert "NOAA" in prof.notes[0]
    assert s.weather_coverage()[0]["hours"] == 5


def test_large_gap_is_rejected():
    rows = [{"ts": "2025-07-01T00:00:00+00:00", "temp_c": 28, "dewpoint_c": 24},
            {"ts": "2025-07-01T12:00:00+00:00", "temp_c": 30, "dewpoint_c": 24}]
    with pytest.raises(ValueError, match="gap"):
        weather.fill_gaps(rows)


def test_seasonal_report_groups_by_month_and_season():
    cfg = example_config()
    rows = []
    t = datetime(2026, 5, 31, 12, tzinfo=timezone.utc)
    for i in range(24 * 4):
        rows.append({"ts": (t + timedelta(hours=i)).isoformat(), "source": "mock", "fans_on": i % 2,
                     "removal_g_h": 200, "power_w": 50 if i % 2 else 0, "indoor_rh": 65.0 + (i % 3),
                     "potential_removal_g_h": 200, "reason_code": "DRYING", "indoor_td_c": 20, "outdoor_td_c": 18})
    r = reports.seasonal(rows, cfg, ZoneInfo("America/Chicago"))
    assert [m["period"] for m in r["months"]] == ["2026-05", "2026-06"]
    assert {s["period"] for s in r["seasons"]} == {"Spring 2026", "Summer 2026"}
    assert r["months"][1]["mean_rh"] == pytest.approx(66, abs=1)


def test_annual_estimate_runs_all_months_and_labels_sources():
    r = reports.annual(example_config(), SimParams(), days_per_month=2, warmup_days=1)
    assert len(r["months"]) == 12 and r["sources"] == ["synthetic"]
    tot = r["totals"]
    assert tot["continuous"]["fan_runtime_h"] == pytest.approx(8760, rel=0.01)
    assert tot["off"]["fan_runtime_h"] == 0
    assert tot["optimized"]["condensation_risk_hours"] == 0
    assert "synthetic" in r["label"]


def test_phase4_api(tmp_path):
    with TestClient(create_app(str(tmp_path / "a.db"), backfill_hours=30, start_loop=False)) as c:
        assert c.get("/api/reports/seasonal?range=7d").json()["kind"] == "seasonal_report"
        cal = c.get("/api/calibration?range=7d").json()
        assert cal["kind"] == "calibration" and any("mock" in s for s in cal["limitations"])
        cov = c.get("/api/weather/coverage").json()
        assert cov["default"] == "72231012916" and cov["cached"] == []
        cfg = c.post("/api/calibration/apply", json={"natural_ach": 0.42, "fan_cfm_total": 120}).json()
        assert cfg["house"]["natural_ach"] == 0.42
        assert sum(f["actual_cfm"] for f in cfg["fans"]) == pytest.approx(120)
        ev = c.get("/api/events?kind=config").json()
        assert any(e["reason_code"] == "CALIBRATION_APPLIED" for e in ev)
