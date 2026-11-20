"""CSV 导入与校验。

所有解析错误都带文件名与行号，便于定位问题记录。
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .models import GovernanceAction, InstrumentPeriod, Observation, WeatherRecord


class IngestionError(ValueError):
    """输入数据不符合约定格式。"""


def _ctx(path: Path, lineno: int) -> str:
    return f"{path.name} 第 {lineno} 行"


def _require_columns(path: Path, header: list[str], required: list[str]) -> dict[str, int]:
    index = {name: i for i, name in enumerate(header)}
    missing = [name for name in required if name not in index]
    if missing:
        raise IngestionError(f"{path.name} 缺少必需列: {', '.join(missing)}")
    return index


def _parse_date(raw: str, ctx: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise IngestionError(f"{ctx}: 日期格式无效 {raw!r}，应为 YYYY-MM-DD") from exc


def _parse_float(raw: str, ctx: str, field: str) -> float:
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise IngestionError(f"{ctx}: 字段 {field} 不是数值 {raw!r}") from exc


def _parse_int(raw: str, ctx: str, field: str) -> int:
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise IngestionError(f"{ctx}: 字段 {field} 不是整数 {raw!r}") from exc


def _read_rows(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    if len(rows) < 1:
        raise IngestionError(f"{path.name} 为空文件")
    header = [cell.strip() for cell in rows[0]]
    body = [
        (lineno, row)
        for lineno, row in enumerate(rows[1:], start=2)
        if any(cell.strip() for cell in row)
    ]
    return header, body


def load_stations(path: Path) -> list[InstrumentPeriod]:
    header, body = _read_rows(path)
    idx = _require_columns(
        path, header, ["station_id", "area_id", "instrument", "valid_from", "valid_to"]
    )
    periods: list[InstrumentPeriod] = []
    for lineno, row in body:
        ctx = _ctx(path, lineno)
        valid_to_raw = row[idx["valid_to"]].strip()
        periods.append(
            InstrumentPeriod(
                station_id=row[idx["station_id"]].strip(),
                area_id=row[idx["area_id"]].strip(),
                instrument=row[idx["instrument"]].strip(),
                valid_from=_parse_date(row[idx["valid_from"]], ctx),
                valid_to=_parse_date(valid_to_raw, ctx) if valid_to_raw else None,
                event=row[idx["event"]].strip() if "event" in idx else "",
            )
        )
    _validate_periods(periods, path)
    return periods


def _validate_periods(periods: list[InstrumentPeriod], path: Path) -> None:
    by_station: dict[str, list[InstrumentPeriod]] = {}
    for period in periods:
        if period.valid_to is not None and period.valid_to < period.valid_from:
            raise IngestionError(
                f"{path.name}: 站点 {period.station_id} 的有效期起止颠倒 "
                f"({period.valid_from} > {period.valid_to})"
            )
        by_station.setdefault(period.station_id, []).append(period)
    for station_id, items in by_station.items():
        items.sort(key=lambda p: p.valid_from)
        for earlier, later in zip(items, items[1:]):
            if earlier.valid_to is None or earlier.valid_to >= later.valid_from:
                raise IngestionError(
                    f"{path.name}: 站点 {station_id} 的仪器有效期重叠或缺失衔接 "
                    f"({earlier.valid_from}..{earlier.valid_to} 与 "
                    f"{later.valid_from}..{later.valid_to})"
                )


def load_observations(path: Path) -> list[Observation]:
    header, body = _read_rows(path)
    idx = _require_columns(
        path,
        header,
        ["station_id", "date", "pm25", "valid_hours", "quality_flag", "obs_version"],
    )
    observations: list[Observation] = []
    for lineno, row in body:
        ctx = _ctx(path, lineno)
        pm25_raw = row[idx["pm25"]].strip()
        valid_hours = _parse_int(row[idx["valid_hours"]], ctx, "valid_hours")
        if not 0 <= valid_hours <= 24:
            raise IngestionError(f"{ctx}: valid_hours 超出 0..24 范围")
        version = _parse_int(row[idx["obs_version"]], ctx, "obs_version")
        if version < 1:
            raise IngestionError(f"{ctx}: obs_version 必须为正整数")
        observations.append(
            Observation(
                station_id=row[idx["station_id"]].strip(),
                day=_parse_date(row[idx["date"]], ctx),
                pm25=_parse_float(pm25_raw, ctx, "pm25") if pm25_raw else None,
                valid_hours=valid_hours,
                quality_flag=row[idx["quality_flag"]].strip(),
                version=version,
            )
        )
    return observations


def load_weather(path: Path) -> list[WeatherRecord]:
    header, body = _read_rows(path)
    idx = _require_columns(
        path, header, ["area_id", "date", "temp_c", "wind_m_s", "rain_mm"]
    )
    records: list[WeatherRecord] = []
    for lineno, row in body:
        ctx = _ctx(path, lineno)
        records.append(
            WeatherRecord(
                area_id=row[idx["area_id"]].strip(),
                day=_parse_date(row[idx["date"]], ctx),
                temp_c=_parse_float(row[idx["temp_c"]], ctx, "temp_c"),
                wind_m_s=_parse_float(row[idx["wind_m_s"]], ctx, "wind_m_s"),
                rain_mm=_parse_float(row[idx["rain_mm"]], ctx, "rain_mm"),
            )
        )
    return records


def load_actions(path: Path) -> list[GovernanceAction]:
    header, body = _read_rows(path)
    idx = _require_columns(
        path, header, ["action_id", "action_type", "area_scope", "start", "end"]
    )
    actions: list[GovernanceAction] = []
    seen: set[str] = set()
    for lineno, row in body:
        ctx = _ctx(path, lineno)
        action_id = row[idx["action_id"]].strip()
        if action_id in seen:
            raise IngestionError(f"{ctx}: action_id 重复 {action_id!r}")
        seen.add(action_id)
        areas = tuple(
            part.strip()
            for part in row[idx["area_scope"]].split(";")
            if part.strip()
        )
        if not areas:
            raise IngestionError(f"{ctx}: area_scope 至少包含一个区域")
        start = _parse_date(row[idx["start"]], ctx)
        end = _parse_date(row[idx["end"]], ctx)
        if end < start:
            raise IngestionError(f"{ctx}: 行动结束日期早于开始日期")
        actions.append(
            GovernanceAction(
                action_id=action_id,
                action_type=row[idx["action_type"]].strip(),
                areas=areas,
                start=start,
                end=end,
                description=row[idx["description"]].strip() if "description" in idx else "",
            )
        )
    return actions
