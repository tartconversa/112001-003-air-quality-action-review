"""生成合成演示数据（确定性，固定随机种子）。

数据叙事：
- S01（North）：2026-04-01 仪器更换（PM25-A -> PM25-B，+3 µg/m³ 偏移）；
  2 月锅炉改造（A01）带来真实下降；2026-02-14 在 v2 中被更正。
- S02（South）：排放平稳；2026-03-10..19 缺测（v1），其中 8 天在 v2 补录；
  4 月有利气象使原始值大降——用于演示"气象解释"。
- S03（East）：2026-03-01 迁址（PM25-A -> PM25-A2），站址环境不同导致
  序列断点，不得与迁址前直接拼接比较。
- A02 道路扬尘治理同时覆盖 North 与 East，用于演示跨区域行动不重复记功。

用法：python scripts/generate_fixtures.py [输出目录]
"""

from __future__ import annotations

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

SEED = 20260101
START = date(2026, 1, 1)
END = date(2026, 5, 31)

STATION_AREA = {"S01": "North", "S02": "South", "S03": "East"}
AREAS = ["North", "South", "East"]

# 仪器/点位有效期（含分段事件）
STATION_ROWS = [
    ("S01", "North", "PM25-A", "2025-01-01", "2026-03-31", ""),
    ("S01", "North", "PM25-B", "2026-04-01", "", "instrument_change"),
    ("S02", "South", "PM25-A", "2025-01-01", "", ""),
    ("S03", "East", "PM25-A", "2025-01-01", "2026-02-28", ""),
    ("S03", "East", "PM25-A2", "2026-03-01", "", "relocation"),
]

INSTRUMENT_OFFSET = {("S01", date(2026, 4, 1)): 3.0}  # PM25-B 系统偏高

MISSING_V1 = {("S02", date(2026, 3, 10) + timedelta(days=i)) for i in range(10)}
BACKFILL_DAYS = sorted(
    ("S02", date(2026, 3, 10) + timedelta(days=i)) for i in range(8)
)
CORRECTED_V1 = {("S01", date(2026, 2, 14)): 25.0}  # v1 异常偏高，v2 更正

TEMP_BASE = {1: 3.0, 2: 6.0, 3: 11.0, 4: 17.0, 5: 22.0}
WIND_BASE = {1: 1.6, 2: 1.8, 3: 2.0, 4: 3.2, 5: 2.8}
RAIN = {
    1: {12: 1.5, 26: 2.0},
    2: {8: 1.0, 19: 2.5},
    3: {6: 1.5, 21: 2.0, 27: 3.0},
    4: {3: 2.5, 7: 4.0, 10: 3.0, 14: 5.5, 18: 2.0, 22: 6.0, 25: 3.5, 28: 4.5},
    5: {5: 3.0, 15: 4.0, 25: 2.5},
}
AREA_TWEAK = {  # (温度偏移, 风速偏移)
    "North": (0.0, 0.0),
    "South": (1.0, 0.2),
    "East": (-0.5, -0.3),
}

ACTION_ROWS = [
    ("A01", "boiler_upgrade", "North", "2026-02-01", "2026-03-01", "北区锅炉低氮改造"),
    ("A02", "road_dust_control", "North;East", "2026-04-10", "2026-04-30",
     "道路扬尘联合治理（跨区域）"),
    ("A03", "inspection", "South", "2026-04-14", "2026-04-20", "南区企业专项检查"),
]


def emission(station: str, day: date) -> float:
    if station == "S01":
        if day < date(2026, 2, 1):
            return 58.0
        if day < date(2026, 5, 1):
            return 50.0
        return 48.0
    if station == "S02":
        return 45.0
    if station == "S03":
        return 52.0 if day < date(2026, 3, 1) else 44.0
    raise KeyError(station)


def met_effect(temp: float, wind: float, rain: float) -> float:
    return -4.0 * (wind - 2.0) - 1.5 * rain - 0.5 * (temp - 8.0)


def instrument_offset(station: str, day: date) -> float:
    if station == "S01" and day >= date(2026, 4, 1):
        return INSTRUMENT_OFFSET[("S01", date(2026, 4, 1))]
    return 0.0


def days() -> list[date]:
    result = []
    current = START
    while current <= END:
        result.append(current)
        current += timedelta(days=1)
    return result


def build_weather(rng: random.Random) -> dict[tuple[str, date], tuple[float, float, float]]:
    weather: dict[tuple[str, date], tuple[float, float, float]] = {}
    for area in AREAS:
        dt, dw = AREA_TWEAK[area]
        for day in days():
            temp = TEMP_BASE[day.month] + dt + rng.gauss(0, 1.0)
            wind = max(0.5, WIND_BASE[day.month] + dw + rng.gauss(0, 0.4))
            rain = RAIN.get(day.month, {}).get(day.day, 0.0)
            weather[(area, day)] = (round(temp, 1), round(wind, 1), round(rain, 1))
    return weather


def build_pm25(rng: random.Random,
               weather: dict) -> dict[tuple[str, date], float]:
    values: dict[tuple[str, date], float] = {}
    for station in STATION_AREA:
        area = STATION_AREA[station]
        for day in days():
            temp, wind, rain = weather[(area, day)]
            value = (emission(station, day) + met_effect(temp, wind, rain)
                     + instrument_offset(station, day) + rng.gauss(0, 2.5))
            values[(station, day)] = max(2.0, round(value, 1))
    return values


def write_csv(path: Path, header: list[str], rows: list[tuple]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    rng = random.Random(SEED)
    weather = build_weather(rng)
    pm25 = build_pm25(rng, weather)

    counts = {}
    counts["stations.csv"] = write_csv(
        out / "stations.csv",
        ["station_id", "area_id", "instrument", "valid_from", "valid_to", "event"],
        STATION_ROWS,
    )

    obs_rows = []
    for station in STATION_AREA:
        for day in days():
            key = (station, day)
            if key in MISSING_V1:
                obs_rows.append((station, day.isoformat(), "", 0, "missing", 1))
                continue
            value = pm25[key] + CORRECTED_V1.get(key, 0.0)
            hours = 20 if day.day % 9 == 0 else 24
            obs_rows.append((station, day.isoformat(), f"{value:.1f}", hours, "valid", 1))
    counts["observations.csv"] = write_csv(
        out / "observations.csv",
        ["station_id", "date", "pm25", "valid_hours", "quality_flag", "obs_version"],
        obs_rows,
    )

    weather_rows = [
        (area, day.isoformat(), *weather[(area, day)])
        for area in AREAS for day in days()
    ]
    counts["weather.csv"] = write_csv(
        out / "weather.csv",
        ["area_id", "date", "temp_c", "wind_m_s", "rain_mm"],
        weather_rows,
    )

    counts["actions.csv"] = write_csv(
        out / "actions.csv",
        ["action_id", "action_type", "area_scope", "start", "end", "description"],
        ACTION_ROWS,
    )

    v2_rows = []
    for station, day in BACKFILL_DAYS:
        v2_rows.append((station, day.isoformat(), f"{pm25[(station, day)]:.1f}",
                        24, "valid", 2))
    for (station, day), spike in sorted(CORRECTED_V1.items()):
        corrected = pm25[(station, day)]
        v2_rows.append((station, day.isoformat(), f"{corrected:.1f}", 24, "valid", 2))
    counts["backfill/observations.csv"] = write_csv(
        out / "backfill" / "observations.csv",
        ["station_id", "date", "pm25", "valid_hours", "quality_flag", "obs_version"],
        v2_rows,
    )

    purposes = {
        "stations.csv": "站点仪器/点位有效期（含仪器更换与迁址事件）",
        "observations.csv": "逐日 PM2.5 观测 v1（含 S02 三月缺测与 S01 待更正值）",
        "weather.csv": "区域逐日气象（气温/风速/降雨）",
        "actions.csv": "治理行动及适用区域范围",
        "backfill/observations.csv": "补录与更正（obs_version=2）",
    }
    write_csv(
        out / "fixture_manifest.csv",
        ["dataset", "records", "purpose"],
        [(name, counts[name], purposes[name]) for name in counts],
    )

    for name, count in counts.items():
        print(f"{name}: {count} 行")


if __name__ == "__main__":
    main()
