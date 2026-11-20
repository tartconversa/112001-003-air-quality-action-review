"""核心数据模型。

观测记录带版本号：同一 (station_id, day) 可出现多行，
版本号最大者为当前有效值，旧版本保留用于审计与变更检测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class InstrumentPeriod:
    """站点仪器/点位有效期。event 标记该段开始时的变更类型。"""

    station_id: str
    area_id: str
    instrument: str
    valid_from: date
    valid_to: date | None  # None 表示至今有效
    event: str = ""  # ""（首段）/ "instrument_change" / "relocation"


@dataclass(frozen=True)
class Observation:
    station_id: str
    day: date
    pm25: float | None  # None 表示缺测
    valid_hours: int
    quality_flag: str
    version: int


@dataclass(frozen=True)
class WeatherRecord:
    area_id: str
    day: date
    temp_c: float
    wind_m_s: float
    rain_mm: float


@dataclass(frozen=True)
class GovernanceAction:
    """治理行动。areas 为适用监测区域；跨区域行动的成效不得重复记功。"""

    action_id: str
    action_type: str
    areas: tuple[str, ...]
    start: date
    end: date
    description: str = ""

    @property
    def shared(self) -> bool:
        return len(self.areas) > 1

    def covers(self, area_id: str) -> bool:
        return area_id in self.areas

    def overlaps(self, start: date, end: date) -> bool:
        return self.start <= end and self.end >= start


@dataclass
class ReviewAction:
    """复核行动：需要复核的变化登记，含责任区域与跟进期限。"""

    review_id: str
    station_id: str
    area_id: str
    period: str  # "2026-04"
    reason: str
    created_at: str  # ISO 时间
    created_obs_version: int
    due_date: str  # ISO 日期，跟进期限
    status: str = "open"  # open / closed
    needs_reevaluation: bool = False
    closed_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "review_id": self.review_id,
            "station_id": self.station_id,
            "area_id": self.area_id,
            "period": self.period,
            "reason": self.reason,
            "created_at": self.created_at,
            "created_obs_version": self.created_obs_version,
            "due_date": self.due_date,
            "status": self.status,
            "needs_reevaluation": self.needs_reevaluation,
            "closed_at": self.closed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ReviewAction":
        return cls(**data)
