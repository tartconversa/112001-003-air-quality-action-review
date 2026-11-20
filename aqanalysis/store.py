"""状态存储：观测版本解析、变更日志、分析与报告快照。

状态目录结构（JSON 文件，仅标准库）：

    <state_dir>/
        meta.json            当前观测版本、最近导入时间
        stations.json        站点仪器有效期
        observations.json    全部观测行（含历史版本，追加合并）
        weather.json         区域逐日气象
        actions.json         治理行动及适用范围
        change_log.json      每次导入引起的有效值变更清单
        analyses/<period>.json   分析快照
        reviews.json         复核行动登记簿
        reports/<period>.json    报告修订序列（追加，不改写）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .ingest import load_actions, load_observations, load_stations, load_weather
from .models import (
    GovernanceAction,
    InstrumentPeriod,
    Observation,
    ReviewAction,
    WeatherRecord,
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Dataset:
    """一次分析所需的全部输入。"""

    stations: list[InstrumentPeriod]
    observations: list[Observation]  # 全部版本行
    weather: list[WeatherRecord]
    actions: list[GovernanceAction]
    obs_version: int


def resolve_observations(observations: list[Observation]) -> dict[tuple[str, date], Observation]:
    """同一 (站点, 日期) 取版本号最大的记录为当前有效值。"""
    resolved: dict[tuple[str, date], Observation] = {}
    for obs in observations:
        key = (obs.station_id, obs.day)
        current = resolved.get(key)
        if current is None or obs.version >= current.version:
            resolved[key] = obs
    return resolved


def _obs_value(obs: Observation | None) -> tuple:
    if obs is None:
        return None
    return (obs.pm25, obs.valid_hours, obs.quality_flag)


def diff_resolved(
    old: dict[tuple[str, date], Observation],
    new: dict[tuple[str, date], Observation],
) -> list[tuple[str, date]]:
    """返回有效值发生变化的 (站点, 日期) 清单（含新增补录）。"""
    changed = []
    for key in old.keys() | new.keys():
        if _obs_value(old.get(key)) != _obs_value(new.get(key)):
            changed.append(key)
    return sorted(changed)


# ---------------------------------------------------------------- 序列化

def _date_to_str(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _date_from_str(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


def _station_to_dict(p: InstrumentPeriod) -> dict:
    return {
        "station_id": p.station_id,
        "area_id": p.area_id,
        "instrument": p.instrument,
        "valid_from": p.valid_from.isoformat(),
        "valid_to": _date_to_str(p.valid_to),
        "event": p.event,
    }


def _station_from_dict(d: dict) -> InstrumentPeriod:
    return InstrumentPeriod(
        station_id=d["station_id"],
        area_id=d["area_id"],
        instrument=d["instrument"],
        valid_from=date.fromisoformat(d["valid_from"]),
        valid_to=_date_from_str(d["valid_to"]),
        event=d.get("event", ""),
    )


def _obs_to_dict(o: Observation) -> dict:
    return {
        "station_id": o.station_id,
        "date": o.day.isoformat(),
        "pm25": o.pm25,
        "valid_hours": o.valid_hours,
        "quality_flag": o.quality_flag,
        "obs_version": o.version,
    }


def _obs_from_dict(d: dict) -> Observation:
    return Observation(
        station_id=d["station_id"],
        day=date.fromisoformat(d["date"]),
        pm25=d["pm25"],
        valid_hours=d["valid_hours"],
        quality_flag=d["quality_flag"],
        version=d["obs_version"],
    )


def _weather_to_dict(w: WeatherRecord) -> dict:
    return {
        "area_id": w.area_id,
        "date": w.day.isoformat(),
        "temp_c": w.temp_c,
        "wind_m_s": w.wind_m_s,
        "rain_mm": w.rain_mm,
    }


def _weather_from_dict(d: dict) -> WeatherRecord:
    return WeatherRecord(
        area_id=d["area_id"],
        day=date.fromisoformat(d["date"]),
        temp_c=d["temp_c"],
        wind_m_s=d["wind_m_s"],
        rain_mm=d["rain_mm"],
    )


def _action_to_dict(a: GovernanceAction) -> dict:
    return {
        "action_id": a.action_id,
        "action_type": a.action_type,
        "areas": list(a.areas),
        "start": a.start.isoformat(),
        "end": a.end.isoformat(),
        "description": a.description,
    }


def _action_from_dict(d: dict) -> GovernanceAction:
    return GovernanceAction(
        action_id=d["action_id"],
        action_type=d["action_type"],
        areas=tuple(d["areas"]),
        start=date.fromisoformat(d["start"]),
        end=date.fromisoformat(d["end"]),
        description=d.get("description", ""),
    )


# ---------------------------------------------------------------- 存储

class Store:
    def __init__(self, state_dir: Path | str):
        self.root = Path(state_dir)

    # -- 基础读写 ------------------------------------------------------

    def _path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def _read_json(self, *parts: str, default=None):
        path = self._path(*parts)
        if not path.exists():
            return default
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)

    def _write_json(self, data, *parts: str) -> None:
        path = self._path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        tmp.replace(path)

    # -- 导入 ----------------------------------------------------------

    def ingest(self, data_dir: Path | str, now: str | None = None) -> dict:
        """导入一个数据目录。

        观测行追加合并（用于补录/更正）；站点、气象、行动为参照数据，
        若目录中提供对应文件则整体替换。返回本次导入摘要。
        """
        data_dir = Path(data_dir)
        now = now or utcnow_iso()

        old_resolved = resolve_observations(self.load_observations())

        summary = {"ingested_at": now, "files": {}, "changed_keys": []}

        stations_file = data_dir / "stations.csv"
        if stations_file.exists():
            stations = load_stations(stations_file)
            self._write_json([_station_to_dict(p) for p in stations], "stations.json")
            summary["files"]["stations"] = len(stations)

        weather_file = data_dir / "weather.csv"
        if weather_file.exists():
            weather = load_weather(weather_file)
            self._write_json([_weather_to_dict(w) for w in weather], "weather.json")
            summary["files"]["weather"] = len(weather)

        actions_file = data_dir / "actions.csv"
        if actions_file.exists():
            actions = load_actions(actions_file)
            self._write_json([_action_to_dict(a) for a in actions], "actions.json")
            summary["files"]["actions"] = len(actions)

        obs_file = data_dir / "observations.csv"
        if obs_file.exists():
            incoming = load_observations(obs_file)
            existing = self.load_observations()
            known = {
                (o.station_id, o.day.isoformat(), o.version, o.pm25,
                 o.valid_hours, o.quality_flag)
                for o in existing
            }
            merged = list(existing)
            added = 0
            for obs in incoming:
                key = (obs.station_id, obs.day.isoformat(), obs.version,
                       obs.pm25, obs.valid_hours, obs.quality_flag)
                if key not in known:
                    merged.append(obs)
                    known.add(key)
                    added += 1
            self._write_json([_obs_to_dict(o) for o in merged], "observations.json")
            summary["files"]["observations"] = added

        new_observations = self.load_observations()
        new_resolved = resolve_observations(new_observations)
        changed = diff_resolved(old_resolved, new_resolved)
        obs_version = max((o.version for o in new_observations), default=0)

        if changed:
            entry = {
                "ingested_at": now,
                "obs_version": obs_version,
                "changed": [
                    {"station_id": s, "date": d.isoformat()} for s, d in changed
                ],
            }
            log = self.change_log()
            log.append(entry)
            self._write_json(log, "change_log.json")
            summary["changed_keys"] = [f"{s}@{d.isoformat()}" for s, d in changed]

        self._write_json(
            {"obs_version": obs_version, "last_ingest_at": now},
            "meta.json",
        )
        summary["obs_version"] = obs_version
        return summary

    # -- 读取 ----------------------------------------------------------

    def meta(self) -> dict:
        return self._read_json("meta.json", default={"obs_version": 0})

    def load_stations(self) -> list[InstrumentPeriod]:
        return [_station_from_dict(d) for d in self._read_json("stations.json", default=[])]

    def load_observations(self) -> list[Observation]:
        return [_obs_from_dict(d) for d in self._read_json("observations.json", default=[])]

    def load_weather(self) -> list[WeatherRecord]:
        return [_weather_from_dict(d) for d in self._read_json("weather.json", default=[])]

    def load_actions(self) -> list[GovernanceAction]:
        return [_action_from_dict(d) for d in self._read_json("actions.json", default=[])]

    def change_log(self) -> list[dict]:
        return self._read_json("change_log.json", default=[])

    def dataset(self) -> Dataset:
        return Dataset(
            stations=self.load_stations(),
            observations=self.load_observations(),
            weather=self.load_weather(),
            actions=self.load_actions(),
            obs_version=self.meta().get("obs_version", 0),
        )

    # -- 分析快照 ------------------------------------------------------

    def save_analysis(self, period: str, analysis: dict) -> None:
        self._write_json(analysis, "analyses", f"{period}.json")

    def load_analysis(self, period: str) -> dict | None:
        return self._read_json("analyses", f"{period}.json")

    def list_analyses(self) -> list[str]:
        directory = self._path("analyses")
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.json"))

    # -- 复核行动 ------------------------------------------------------

    def load_reviews(self) -> list[ReviewAction]:
        return [ReviewAction.from_dict(d) for d in self._read_json("reviews.json", default=[])]

    def save_reviews(self, reviews: list[ReviewAction]) -> None:
        self._write_json([r.to_dict() for r in reviews], "reviews.json")

    # -- 报告 ----------------------------------------------------------

    def load_report(self, period: str) -> dict:
        return self._read_json("reports", f"{period}.json", default={"period": period, "revisions": []})

    def save_report(self, period: str, report: dict) -> None:
        self._write_json(report, "reports", f"{period}.json")

    def list_reports(self) -> list[str]:
        directory = self._path("reports")
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.json"))
