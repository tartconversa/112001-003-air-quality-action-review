"""站点序列分段：仪器更换与迁址把序列切成不可直接拼接的段。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import InstrumentPeriod


@dataclass(frozen=True)
class Segment:
    station_id: str
    area_id: str
    instrument: str
    start: date
    end: date | None
    event: str  # 段起始事件："" / "instrument_change" / "relocation"
    index: int  # 站内段序号，从 0 开始

    def contains(self, day: date) -> bool:
        if day < self.start:
            return False
        if self.end is not None and day > self.end:
            return False
        return True

    @property
    def label(self) -> str:
        return f"{self.station_id}#seg{self.index}({self.instrument})"


def build_segments(periods: list[InstrumentPeriod]) -> dict[str, list[Segment]]:
    """按站点构建有序分段。输入应已通过 ingest 的重叠校验。"""
    by_station: dict[str, list[InstrumentPeriod]] = {}
    for period in periods:
        by_station.setdefault(period.station_id, []).append(period)
    result: dict[str, list[Segment]] = {}
    for station_id, items in by_station.items():
        items.sort(key=lambda p: p.valid_from)
        result[station_id] = [
            Segment(
                station_id=station_id,
                area_id=p.area_id,
                instrument=p.instrument,
                start=p.valid_from,
                end=p.valid_to,
                event=p.event,
                index=i,
            )
            for i, p in enumerate(items)
        ]
    return result


def segment_for(segments: dict[str, list[Segment]], station_id: str, day: date) -> Segment | None:
    for segment in segments.get(station_id, []):
        if segment.contains(day):
            return segment
    return None


def same_segment(a: Segment | None, b: Segment | None) -> bool:
    """两期是否处于同一段。跨段（仪器更换/迁址）不得直接比较。"""
    if a is None or b is None:
        return False
    return a.station_id == b.station_id and a.index == b.index
