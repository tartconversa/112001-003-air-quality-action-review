"""月度趋势分析与归因。

归因决策遵循固定顺序，任何一步资料不足都止步于
"不作确定性归因"，而不是给出看似精确的结论：

1. 无上一可比期            -> no_previous
2. 跨仪器/迁址段边界       -> boundary_confounded（不直接比较）
3. 任一期覆盖不足          -> insufficient_data
4. 气象校正可用            -> met_explained / real_change / no_significant_change
5. 气象校正不可用且变化显著 -> met_unavailable（登记复核，不归因）
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date

from . import method
from .coverage import (
    COMPLIANCE_LABELS,
    COVERAGE_LABELS,
    MonthCoverage,
    assess_month,
    compliance_status,
    is_valid_day,
    month_days,
)
from .metadjust import Adjustment, collect_pairs, monthly_adjustment
from .models import GovernanceAction
from .segments import Segment, build_segments, same_segment, segment_for
from .store import Dataset, resolve_observations

# 归因代码 -> 中文表述
ATTRIBUTION_LABELS = {
    "no_previous": "无上一可比期",
    "boundary_confounded": "跨仪器更换/迁址边界，不作直接比较",
    "insufficient_data": "观测覆盖不足，不作归因",
    "met_explained": "原始变化主要由气象条件解释",
    "real_change": "扣除气象影响后仍有显著变化",
    "no_significant_change": "未见显著变化",
    "met_unavailable": "气象校正资料不足，无法确定归因",
}


@dataclass
class StationMonth:
    station_id: str
    area_id: str
    period: str
    segment: Segment | None
    coverage: MonthCoverage
    raw_mean: float | None
    adjustment: Adjustment | None
    compliance: str

    def to_dict(self) -> dict:
        return {
            "station_id": self.station_id,
            "area_id": self.area_id,
            "period": self.period,
            "segment": (
                {
                    "index": self.segment.index,
                    "instrument": self.segment.instrument,
                    "event": self.segment.event,
                }
                if self.segment
                else None
            ),
            "coverage": self.coverage.to_dict(),
            "raw_mean": round(self.raw_mean, 3) if self.raw_mean is not None else None,
            "adjustment": self.adjustment.to_dict() if self.adjustment else None,
            "compliance": self.compliance,
            "compliance_label": COMPLIANCE_LABELS[self.compliance],
        }


@dataclass
class ChangeAssessment:
    raw_change: float | None
    adjusted_change: float | None
    weather_explained: float | None
    crosses_boundary: bool
    attribution: str
    detail: str

    @property
    def significant(self) -> bool:
        return self.attribution in {"real_change", "met_unavailable"}

    def to_dict(self) -> dict:
        return {
            "raw_change": round(self.raw_change, 3) if self.raw_change is not None else None,
            "adjusted_change": (
                round(self.adjusted_change, 3) if self.adjusted_change is not None else None
            ),
            "weather_explained": (
                round(self.weather_explained, 3) if self.weather_explained is not None else None
            ),
            "crosses_boundary": self.crosses_boundary,
            "attribution": self.attribution,
            "attribution_label": ATTRIBUTION_LABELS[self.attribution],
            "detail": self.detail,
        }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def station_month(
    dataset: Dataset,
    segments: dict[str, list[Segment]],
    resolved: dict,
    weather_by_area_day: dict,
    station_id: str,
    year: int,
    month: int,
) -> StationMonth:
    period = f"{year:04d}-{month:02d}"
    station_segments = segments.get(station_id, [])
    if not station_segments:
        raise KeyError(f"站点 {station_id} 没有仪器有效期配置")
    area_id = station_segments[0].area_id
    mid = date(year, month, 15)
    segment = segment_for(segments, station_id, mid)
    coverage = assess_month(station_id, year, month, resolved, segment)

    days = month_days(year, month)
    valid_values = [
        resolved[(station_id, d)].pm25
        for d in days
        if is_valid_day(resolved.get((station_id, d)))
    ]
    raw_mean = _mean(valid_values)

    adjustment = None
    if segment is not None:
        segment_days = _segment_days(segment)
        segment_pairs = collect_pairs(station_id, segment_days, resolved, weather_by_area_day, area_id)
        month_pairs = collect_pairs(station_id, days, resolved, weather_by_area_day, area_id)
        adjustment = monthly_adjustment(month_pairs, segment_pairs)

    return StationMonth(
        station_id=station_id,
        area_id=area_id,
        period=period,
        segment=segment,
        coverage=coverage,
        raw_mean=raw_mean,
        adjustment=adjustment,
        compliance=compliance_status(raw_mean, coverage),
    )


def _segment_days(segment: Segment) -> list[date]:
    end = segment.end or date.today()
    days = []
    current = segment.start
    while current <= end:
        days.append(current)
        current = date.fromordinal(current.toordinal() + 1)
    return days


def assess_change(
    prev: StationMonth | None,
    curr: StationMonth,
) -> ChangeAssessment:
    raw_change = None
    if prev is not None and prev.raw_mean is not None and curr.raw_mean is not None:
        raw_change = curr.raw_mean - prev.raw_mean

    if prev is None:
        return ChangeAssessment(None, None, None, False, "no_previous", "首期，无环比对象")

    if prev.segment is None:
        return ChangeAssessment(
            raw_change, None, None, False, "no_previous",
            "上一期无仪器有效期配置，无可比序列",
        )
    if curr.segment is None:
        return ChangeAssessment(
            raw_change, None, None, False, "insufficient_data",
            "本期无仪器有效期配置，不作归因",
        )

    crosses = not same_segment(prev.segment, curr.segment)
    if crosses:
        event = curr.segment.event if curr.segment else ""
        label = {"instrument_change": "仪器更换", "relocation": "迁址"}.get(event, "序列分段")
        return ChangeAssessment(
            raw_change,
            None,
            None,
            True,
            "boundary_confounded",
            f"两期之间存在{label}（{prev.segment.label} -> {curr.segment.label}），"
            "序列不得静默拼接，变化量仅供参考",
        )

    if not (prev.coverage.sufficient and curr.coverage.sufficient):
        return ChangeAssessment(
            raw_change,
            None,
            None,
            False,
            "insufficient_data",
            f"覆盖不足（上期 {COVERAGE_LABELS[prev.coverage.level]}，"
            f"本期 {COVERAGE_LABELS[curr.coverage.level]}），不作归因",
        )

    if prev.adjustment is not None and curr.adjustment is not None:
        adjusted_change = curr.adjustment.adjusted_mean - prev.adjustment.adjusted_mean
        weather_explained = (raw_change or 0.0) - adjusted_change
        if abs(raw_change or 0.0) < method.SIGNIFICANT_CHANGE_UG:
            attribution, detail = "no_significant_change", (
                f"原始变化 {raw_change:+.1f} µg/m³，低于显著性阈值"
            )
        elif abs(adjusted_change) < method.EPSILON_UG:
            attribution, detail = "met_explained", (
                f"原始变化 {raw_change:+.1f} µg/m³，扣除气象影响后残余 "
                f"{adjusted_change:+.1f} µg/m³，在噪声带宽内"
            )
        else:
            direction = "改善" if adjusted_change < 0 else "恶化"
            attribution, detail = "real_change", (
                f"扣除气象影响后仍{direction} {abs(adjusted_change):.1f} µg/m³"
                f"（原始 {raw_change:+.1f}，气象贡献 {weather_explained:+.1f}）"
            )
        return ChangeAssessment(
            raw_change, adjusted_change, weather_explained, False, attribution, detail
        )

    if raw_change is not None and abs(raw_change) >= method.SIGNIFICANT_CHANGE_UG:
        return ChangeAssessment(
            raw_change,
            None,
            None,
            False,
            "met_unavailable",
            f"原始变化 {raw_change:+.1f} µg/m³ 显著，但气象校正资料不足"
            "（段内有效拟合点不够），无法区分气象影响，需复核",
        )
    return ChangeAssessment(
        raw_change, None, None, False, "no_significant_change",
        "原始变化不显著" if raw_change is not None else "有效数据过少",
    )


# ---------------------------------------------------------------- 区域聚合

@dataclass
class AreaMonth:
    area_id: str
    period: str
    members: list[str]           # 纳入聚合的站点
    excluded: list[str]          # 因覆盖不足被排除的站点
    coverage_ratio: float
    sufficient: bool
    raw_mean: float | None
    adjusted_mean: float | None
    compliance: str

    def to_dict(self) -> dict:
        return {
            "area_id": self.area_id,
            "period": self.period,
            "members": self.members,
            "excluded": self.excluded,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "sufficient": self.sufficient,
            "raw_mean": round(self.raw_mean, 3) if self.raw_mean is not None else None,
            "adjusted_mean": (
                round(self.adjusted_mean, 3) if self.adjusted_mean is not None else None
            ),
            "compliance": self.compliance,
            "compliance_label": COMPLIANCE_LABELS[self.compliance],
        }


def area_month(area_id: str, period: str, stats: list[StationMonth]) -> AreaMonth:
    members = [s for s in stats if s.coverage.sufficient and s.raw_mean is not None]
    excluded = [s.station_id for s in stats if s not in members]
    total_expected = sum(s.coverage.expected_days for s in stats)
    total_valid = sum(s.coverage.valid_days for s in stats)
    ratio = total_valid / total_expected if total_expected else 0.0
    sufficient = ratio >= method.COVERAGE_SUFFICIENT and bool(members)

    def weighted(adjusted: bool) -> float | None:
        num = den = 0.0
        for s in members:
            value = (s.adjustment.adjusted_mean if s.adjustment else None) \
                if adjusted else s.raw_mean
            if value is None:
                continue
            num += value * s.coverage.valid_days
            den += s.coverage.valid_days
        return num / den if den else None

    raw = weighted(adjusted=False)
    pseudo = MonthCoverage(area_id, period, total_expected, total_valid,
                           total_expected - total_valid, ratio,
                           "sufficient" if sufficient else "insufficient")
    return AreaMonth(
        area_id=area_id,
        period=period,
        members=[s.station_id for s in members],
        excluded=excluded,
        coverage_ratio=ratio,
        sufficient=sufficient,
        raw_mean=raw,
        adjusted_mean=weighted(adjusted=True),
        compliance=compliance_status(raw, pseudo),
    )


def assess_area_change(prev: AreaMonth | None, curr: AreaMonth,
                       any_boundary: bool) -> ChangeAssessment:
    raw_change = None
    if prev is not None and prev.raw_mean is not None and curr.raw_mean is not None:
        raw_change = curr.raw_mean - prev.raw_mean
    if prev is None:
        return ChangeAssessment(None, None, None, False, "no_previous", "首期，无环比对象")
    if any_boundary:
        return ChangeAssessment(
            raw_change, None, None, True, "boundary_confounded",
            "区域内存在站点仪器更换/迁址边界，区域变化不作直接归因",
        )
    if not (prev.sufficient and curr.sufficient):
        return ChangeAssessment(
            raw_change, None, None, False, "insufficient_data",
            "区域覆盖不足，不作归因",
        )
    if prev.adjusted_mean is not None and curr.adjusted_mean is not None:
        adjusted_change = curr.adjusted_mean - prev.adjusted_mean
        weather_explained = (raw_change or 0.0) - adjusted_change
        if abs(raw_change or 0.0) < method.SIGNIFICANT_CHANGE_UG:
            return ChangeAssessment(raw_change, adjusted_change, weather_explained,
                                    False, "no_significant_change",
                                    f"区域原始变化 {raw_change:+.1f} µg/m³，不显著")
        if abs(adjusted_change) < method.EPSILON_UG:
            return ChangeAssessment(raw_change, adjusted_change, weather_explained,
                                    False, "met_explained",
                                    f"区域原始变化 {raw_change:+.1f} µg/m³ 主要由气象条件解释")
        direction = "改善" if adjusted_change < 0 else "恶化"
        return ChangeAssessment(raw_change, adjusted_change, weather_explained,
                                False, "real_change",
                                f"区域扣除气象影响后仍{direction} {abs(adjusted_change):.1f} µg/m³")
    if raw_change is not None and abs(raw_change) >= method.SIGNIFICANT_CHANGE_UG:
        return ChangeAssessment(raw_change, None, None, False, "met_unavailable",
                                "区域原始变化显著，但气象校正资料不足，无法确定归因")
    return ChangeAssessment(raw_change, None, None, False, "no_significant_change",
                            "区域原始变化不显著" if raw_change is not None else "有效数据过少")


# ---------------------------------------------------------------- 治理行动匹配

def match_actions(area_id: str, window_start: date, window_end: date,
                  actions: list[GovernanceAction]) -> list[dict]:
    """窗口期内覆盖该区域的治理行动，跨区域行动标注适用范围。"""
    matched = []
    for action in actions:
        if not action.covers(area_id):
            continue
        if not action.overlaps(window_start, window_end):
            continue
        matched.append({
            "action_id": action.action_id,
            "action_type": action.action_type,
            "areas": list(action.areas),
            "shared": action.shared,
            "start": action.start.isoformat(),
            "end": action.end.isoformat(),
            "description": action.description,
            "scope_note": (
                f"该行动同时覆盖 {'、'.join(action.areas)}，成效不得在各区域重复记功"
                if action.shared else ""
            ),
        })
    return matched


def city_action_summary(area_matches: dict[str, list[dict]]) -> list[dict]:
    """全市汇总：同一行动只计一次，并列明其覆盖的所有区域。"""
    seen: dict[str, dict] = {}
    for area_id, matches in area_matches.items():
        for match in matches:
            entry = seen.setdefault(match["action_id"], {
                "action_id": match["action_id"],
                "action_type": match["action_type"],
                "areas": list(match["areas"]),
                "shared": match["shared"],
                "credited_areas": [],
            })
            entry["credited_areas"].append(area_id)
    summary = []
    for entry in seen.values():
        entry = dict(entry)
        entry["dedupe_note"] = (
            "同一变化不因该行动覆盖多区域而重复记功" if entry["shared"] else ""
        )
        summary.append(entry)
    return sorted(summary, key=lambda e: e["action_id"])


# ---------------------------------------------------------------- 主流程

def previous_period(period: str) -> str | None:
    year, month = int(period[:4]), int(period[5:7])
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def analyze_period(dataset: Dataset, period: str, now: str) -> dict:
    """对一个月度期间执行完整分析，返回可序列化的分析快照。"""
    year, month = int(period[:4]), int(period[5:7])
    segments = build_segments(dataset.stations)
    resolved = resolve_observations(dataset.observations)
    weather_by_area_day = {(w.area_id, w.day): w for w in dataset.weather}

    prev = previous_period(period)
    station_ids = sorted(segments.keys())

    stats: dict[str, StationMonth] = {}
    prev_stats: dict[str, StationMonth] = {}
    for station_id in station_ids:
        stats[station_id] = station_month(
            dataset, segments, resolved, weather_by_area_day, station_id, year, month)
        if prev:
            py, pm = int(prev[:4]), int(prev[5:7])
            prev_stats[station_id] = station_month(
                dataset, segments, resolved, weather_by_area_day, station_id, py, pm)

    station_results = {}
    for station_id in station_ids:
        change = assess_change(prev_stats.get(station_id), stats[station_id])
        station_results[station_id] = {
            **stats[station_id].to_dict(),
            "change": change.to_dict(),
        }

    area_ids = sorted({s.area_id for s in dataset.stations})
    window_start = date(year, month, 1)
    if prev:
        window_start = date(int(prev[:4]), int(prev[5:7]), 1)
    window_end = date(year, month, calendar.monthrange(year, month)[1])

    area_results = {}
    area_matches: dict[str, list[dict]] = {}
    for area_id in area_ids:
        member_ids = [sid for sid in station_ids
                      if segments[sid][0].area_id == area_id]
        area_stats = [stats[sid] for sid in member_ids]
        area_prev_stats = [prev_stats[sid] for sid in member_ids if sid in prev_stats]
        current = area_month(area_id, period, area_stats)
        previous = area_month(area_id, prev, area_prev_stats) if prev and area_prev_stats else None
        any_boundary = any(
            station_results[sid]["change"]["crosses_boundary"] for sid in member_ids
        )
        change = assess_area_change(previous, current, any_boundary)
        matches = match_actions(area_id, window_start, window_end, dataset.actions)
        area_matches[area_id] = matches
        area_results[area_id] = {
            **current.to_dict(),
            "change": change.to_dict(),
            "matched_actions": matches,
        }

    return {
        "period": period,
        "generated_at": now,
        "obs_version": dataset.obs_version,
        "method": method.method_dict(),
        "stations": station_results,
        "areas": area_results,
        "city_actions": city_action_summary(area_matches),
    }
