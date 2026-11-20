"""气象校正：区分原始变化与气象条件差异。

方法：在站点当前段内，用有效日的 pm25 对 (1, 气温, 风速, 降雨)
做普通最小二乘拟合；当月气象效应 = 当月逐日拟合均值 - 段内参考
气象（逐日气象均值）的拟合值；校正值 = 实测均值 - 气象效应。

资料不足（有效拟合点少于阈值或方程奇异）时不产出校正值，
调用方不得据此给出确定性归因。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import method
from .coverage import is_valid_day
from .models import Observation, WeatherRecord


@dataclass
class Adjustment:
    raw_mean: float
    weather_effect: float  # 当月气象相对段内参考的贡献（µg/m³）
    adjusted_mean: float
    n_month_days: int
    n_fit_points: int

    def to_dict(self) -> dict:
        return {
            "raw_mean": round(self.raw_mean, 3),
            "weather_effect": round(self.weather_effect, 3),
            "adjusted_mean": round(self.adjusted_mean, 3),
            "n_month_days": self.n_month_days,
            "n_fit_points": self.n_fit_points,
        }


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """高斯消元（部分主元）。奇异时返回 None。"""
    n = len(rhs)
    aug = [row[:] + [b] for row, b in zip(matrix, rhs)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-10:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        for row in range(col + 1, n):
            factor = aug[row][col] / aug[col][col]
            for k in range(col, n + 1):
                aug[row][k] -= factor * aug[col][k]
    solution = [0.0] * n
    for row in range(n - 1, -1, -1):
        residual = sum(aug[row][k] * solution[k] for k in range(row + 1, n))
        solution[row] = (aug[row][n] - residual) / aug[row][row]
    return solution


def fit_ols(pairs: list[tuple[WeatherRecord, float]]) -> list[float] | None:
    """拟合 pm25 ~ 1 + temp_c + wind_m_s + rain_mm，返回系数或 None。

    段内无变化的气象项（如整个时段无降雨）会被剔除后再拟合，
    对应系数记 0，避免设计矩阵奇异。
    """
    if len(pairs) < method.MIN_FIT_POINTS:
        return None
    columns = [
        [1.0] * len(pairs),
        [r.temp_c for r, _ in pairs],
        [r.wind_m_s for r, _ in pairs],
        [r.rain_mm for r, _ in pairs],
    ]
    keep = [0]
    for i in (1, 2, 3):
        col = columns[i]
        mean = sum(col) / len(col)
        variance = sum((v - mean) ** 2 for v in col) / len(col)
        if variance > 1e-12:
            keep.append(i)
    size = len(keep)
    xtx = [[0.0] * size for _ in range(size)]
    xty = [0.0] * size
    for row, (record, value) in enumerate(pairs):
        x = [columns[i][row] for i in keep]
        for i in range(size):
            xty[i] += x[i] * value
            for j in range(size):
                xtx[i][j] += x[i] * x[j]
    reduced = _solve(xtx, xty)
    if reduced is None:
        return None
    beta = [0.0] * 4
    for position, original in enumerate(keep):
        beta[original] = reduced[position]
    return beta


def _predict(beta: list[float], record: WeatherRecord) -> float:
    return (
        beta[0]
        + beta[1] * record.temp_c
        + beta[2] * record.wind_m_s
        + beta[3] * record.rain_mm
    )


def monthly_adjustment(
    month_pairs: list[tuple[WeatherRecord, float]],
    segment_pairs: list[tuple[WeatherRecord, float]],
) -> Adjustment | None:
    """计算某月的气象校正结果；资料不足时返回 None。

    month_pairs：当月有效日的 (气象, 浓度)。
    segment_pairs：站点当前段内全部有效日的 (气象, 浓度)，用于拟合与参考气象。
    """
    if not month_pairs:
        return None
    beta = fit_ols(segment_pairs)
    if beta is None:
        return None
    raw_mean = sum(value for _, value in month_pairs) / len(month_pairs)
    ref = WeatherRecord(
        area_id="",
        day=segment_pairs[0][0].day,
        temp_c=sum(r.temp_c for r, _ in segment_pairs) / len(segment_pairs),
        wind_m_s=sum(r.wind_m_s for r, _ in segment_pairs) / len(segment_pairs),
        rain_mm=sum(r.rain_mm for r, _ in segment_pairs) / len(segment_pairs),
    )
    month_fit = sum(_predict(beta, r) for r, _ in month_pairs) / len(month_pairs)
    weather_effect = month_fit - _predict(beta, ref)
    return Adjustment(
        raw_mean=raw_mean,
        weather_effect=weather_effect,
        adjusted_mean=raw_mean - weather_effect,
        n_month_days=len(month_pairs),
        n_fit_points=len(segment_pairs),
    )


def collect_pairs(
    station_id: str,
    days: list,
    resolved: dict,
    weather_by_area_day: dict,
    area_id: str,
) -> list[tuple[WeatherRecord, float]]:
    """提取指定日期集合内的有效 (气象, 浓度) 对。"""
    pairs = []
    for day in days:
        obs: Observation | None = resolved.get((station_id, day))
        if not is_valid_day(obs):
            continue
        record = weather_by_area_day.get((area_id, day))
        if record is None:
            continue
        pairs.append((record, obs.pm25))
    return pairs
