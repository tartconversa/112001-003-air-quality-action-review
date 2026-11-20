"""观测覆盖评估与达标判定。

原则：缺测不当作达标。月均值只统计有效日；覆盖不足时
不给出达标结论，状态为"数据不足"。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from . import method
from .models import Observation
from .segments import Segment


@dataclass
class MonthCoverage:
    station_id: str
    period: str  # "2026-04"
    expected_days: int  # 段有效期与当月的交集天数
    valid_days: int
    missing_days: int  # 应测而无有效观测的天数
    ratio: float
    level: str  # "sufficient" / "limited" / "insufficient" / "inactive"

    @property
    def sufficient(self) -> bool:
        return self.level == "sufficient"

    def to_dict(self) -> dict:
        return {
            "station_id": self.station_id,
            "period": self.period,
            "expected_days": self.expected_days,
            "valid_days": self.valid_days,
            "missing_days": self.missing_days,
            "ratio": round(self.ratio, 4),
            "level": self.level,
        }


def is_valid_day(obs: Observation | None) -> bool:
    """有效日：有记录、标记 valid、有浓度值、有效小时数达标。"""
    if obs is None:
        return False
    if obs.quality_flag != "valid":
        return False
    if obs.pm25 is None:
        return False
    return obs.valid_hours >= method.MIN_VALID_HOURS


def month_days(year: int, month: int) -> list[date]:
    days = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, days + 1)]


def assess_month(
    station_id: str,
    year: int,
    month: int,
    resolved: dict[tuple[str, date], Observation],
    segment: Segment | None,
) -> MonthCoverage:
    period = f"{year:04d}-{month:02d}"
    if segment is None:
        return MonthCoverage(station_id, period, 0, 0, 0, 0.0, "inactive")
    days = [d for d in month_days(year, month) if segment.contains(d)]
    valid = sum(1 for d in days if is_valid_day(resolved.get((station_id, d))))
    expected = len(days)
    missing = expected - valid
    ratio = valid / expected if expected else 0.0
    if expected == 0:
        level = "inactive"
    elif ratio >= method.COVERAGE_SUFFICIENT:
        level = "sufficient"
    elif ratio >= method.COVERAGE_LIMITED:
        level = "limited"
    else:
        level = "insufficient"
    return MonthCoverage(station_id, period, expected, valid, missing, ratio, level)


def compliance_status(mean_pm25: float | None, coverage: MonthCoverage) -> str:
    """达标判定。覆盖不足时不得给出达标/不达标结论。"""
    if not coverage.sufficient or mean_pm25 is None:
        return "undetermined"
    return "compliant" if mean_pm25 <= method.COMPLIANCE_LIMIT_UG else "non_compliant"


COMPLIANCE_LABELS = {
    "compliant": "达标",
    "non_compliant": "未达标",
    "undetermined": "数据不足，不作达标判定",
}

COVERAGE_LABELS = {
    "sufficient": "充足",
    "limited": "不足（谨慎使用）",
    "insufficient": "不足",
    "inactive": "无有效期配置",
}
