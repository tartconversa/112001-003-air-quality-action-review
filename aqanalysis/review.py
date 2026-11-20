"""复核行动登记簿：需要复核的变化登记、跟进期限、再评估标记。

触发复核的情形：
- real_change      扣除气象影响后仍有显著变化
- met_unavailable  原始变化显著但气象校正资料不足
- 覆盖不足          月度观测覆盖不达标
- 边界附近显著变化   仪器更换/迁址边界两侧原始变化显著

后续补录或更正观测时，未关闭行动若涉及被变更的站点与日期，
标记 needs_reevaluation，提示分析人员重新评估。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

from . import method
from .models import ReviewAction

# 归因代码 -> 复核原因
_REVIEW_REASONS = {
    "real_change": "扣除气象影响后仍有显著变化，需现场复核",
    "met_unavailable": "原始变化显著但气象资料不足，无法归因，需补充复核",
}


def _review_id(station_id: str, period: str, reason: str) -> str:
    digest = hashlib.sha1(f"{station_id}|{period}|{reason}".encode()).hexdigest()[:6]
    return f"R-{period.replace('-', '')}-{station_id}-{digest}"


def required_reviews(analysis: dict, now: str) -> list[ReviewAction]:
    """根据分析快照推导应存在的复核行动。"""
    created: list[ReviewAction] = []
    created_date = now[:10]
    due = (date.fromisoformat(created_date) + timedelta(days=method.FOLLOW_UP_DAYS)).isoformat()
    for station_id, result in analysis["stations"].items():
        period = analysis["period"]
        reasons: list[str] = []
        attribution = result["change"]["attribution"]
        if attribution in _REVIEW_REASONS:
            reasons.append(_REVIEW_REASONS[attribution])
        if result["coverage"]["level"] in {"limited", "insufficient"}:
            reasons.append(
                f"观测覆盖不足（{result['coverage']['level']}），"
                "达标判定与归因受限，需复核监测运行"
            )
        change = result["change"]
        if (
            change["crosses_boundary"]
            and change["raw_change"] is not None
            and abs(change["raw_change"]) >= method.SIGNIFICANT_CHANGE_UG
        ):
            reasons.append("仪器更换/迁址边界附近原始变化显著，需核实是否由序列断点造成")
        for reason in reasons:
            created.append(ReviewAction(
                review_id=_review_id(station_id, period, reason),
                station_id=station_id,
                area_id=result["area_id"],
                period=period,
                reason=reason,
                created_at=now,
                created_obs_version=analysis["obs_version"],
                due_date=due,
            ))
    return created


def register_reviews(existing: list[ReviewAction],
                     required: list[ReviewAction]) -> tuple[list[ReviewAction], list[ReviewAction]]:
    """合并登记簿：同一复核（同 id）已存在（无论开关状态）则不重复登记。

    返回 (完整登记簿, 本次新增)。
    """
    known = {r.review_id for r in existing}
    added = [r for r in required if r.review_id not in known]
    return existing + added, added


def _analysis_window(period: str) -> set[tuple[int, int]]:
    """分析依赖的月份：本期及其上一期（环比比较）。"""
    year, month = int(period[:4]), int(period[5:7])
    if month == 1:
        return {(year, month), (year - 1, 12)}
    return {(year, month), (year, month - 1)}


def flag_reevaluation(reviews: list[ReviewAction], change_log: list[dict]) -> list[ReviewAction]:
    """根据变更日志标记需要重新评估的未关闭行动。"""
    for review in reviews:
        if review.status != "open" or review.needs_reevaluation:
            continue
        window = _analysis_window(review.period)
        for entry in change_log:
            if entry["obs_version"] <= review.created_obs_version:
                continue
            for item in entry["changed"]:
                if item["station_id"] != review.station_id:
                    continue
                day = date.fromisoformat(item["date"])
                if (day.year, day.month) in window:
                    review.needs_reevaluation = True
                    break
            if review.needs_reevaluation:
                break
    return reviews


def close_review(reviews: list[ReviewAction], review_id: str, now: str) -> ReviewAction:
    for review in reviews:
        if review.review_id == review_id:
            if review.status == "closed":
                raise KeyError(f"复核行动 {review_id} 已关闭")
            review.status = "closed"
            review.closed_at = now
            return review
    raise KeyError(f"复核行动 {review_id} 不存在")


def analysis_staleness(analysis: dict, change_log: list[dict]) -> tuple[bool, list[str]]:
    """分析快照是否因后续观测补录/更正而需要重新评估。"""
    window = _analysis_window(analysis["period"])
    stations = set(analysis["stations"].keys())
    reasons: list[str] = []
    for entry in change_log:
        if entry["obs_version"] <= analysis["obs_version"]:
            continue
        hits = sorted({
            item["station_id"]
            for item in entry["changed"]
            if item["station_id"] in stations
            and (date.fromisoformat(item["date"]).year,
                 date.fromisoformat(item["date"]).month) in window
        })
        if hits:
            reasons.append(
                f"观测版本 v{entry['obs_version']} 变更涉及本站期: {', '.join(hits)}"
            )
    return (bool(reasons), reasons)
