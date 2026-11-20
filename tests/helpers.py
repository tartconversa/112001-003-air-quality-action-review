"""测试辅助：构造小型数据集。"""

from __future__ import annotations

from datetime import date, timedelta

from aqanalysis.models import (
    GovernanceAction,
    InstrumentPeriod,
    Observation,
    WeatherRecord,
)
from aqanalysis.store import Dataset


def make_period(station="S1", area="A", instrument="I1",
                start="2026-01-01", end=None, event=""):
    return InstrumentPeriod(
        station_id=station,
        area_id=area,
        instrument=instrument,
        valid_from=date.fromisoformat(start),
        valid_to=date.fromisoformat(end) if end else None,
        event=event,
    )


def make_obs(station, day, pm25, hours=24, flag="valid", version=1):
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return Observation(station, day, pm25, hours, flag, version)


def make_weather(area, day, temp=10.0, wind=2.0, rain=0.0):
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return WeatherRecord(area, day, temp, wind, rain)


def make_action(action_id="A1", areas=("A",), start="2026-01-10", end="2026-01-20"):
    return GovernanceAction(
        action_id=action_id,
        action_type="test_action",
        areas=tuple(areas),
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
    )


def month_days_list(year, month):
    start = date(year, month, 1)
    days = []
    while start.month == month:
        days.append(start)
        start += timedelta(days=1)
    return days


def fill_month(station, year, month, pm25, area="A", weather=None,
               skip=(), hours=24):
    """生成某站整月有效观测与配套气象。skip 中的日期记为缺测。"""
    observations, weather_rows = [], []
    for day in month_days_list(year, month):
        if day in skip:
            observations.append(make_obs(station, day, None, hours=0, flag="missing"))
        else:
            observations.append(make_obs(station, day, pm25, hours=hours))
        w = weather(day) if weather else make_weather(area, day)
        weather_rows.append(w)
    return observations, weather_rows


def make_dataset(stations, observations, weather, actions=(), obs_version=1):
    return Dataset(
        stations=list(stations),
        observations=list(observations),
        weather=list(weather),
        actions=list(actions),
        obs_version=obs_version,
    )
