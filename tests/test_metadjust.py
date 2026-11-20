import unittest
from datetime import date, timedelta

from aqanalysis import method
from aqanalysis.metadjust import fit_ols, monthly_adjustment

from helpers import make_weather


def synthetic_pairs(n, beta, start=date(2026, 1, 1)):
    """按已知系数生成 (气象, 浓度) 对：pm25 = beta·(1,t,w,r)。"""
    pairs = []
    for i in range(n):
        day = start + timedelta(days=i)
        temp = 5.0 + (i % 15)
        wind = 1.0 + (i % 7) * 0.4
        rain = float((i % 5) == 0) * 2.0
        value = beta[0] + beta[1] * temp + beta[2] * wind + beta[3] * rain
        pairs.append((make_weather("A", day, temp, wind, rain), value))
    return pairs


class FitOlsTests(unittest.TestCase):
    def test_recovers_known_coefficients(self):
        beta = [40.0, -0.5, -4.0, -1.5]
        pairs = synthetic_pairs(60, beta)
        fitted = fit_ols(pairs)
        self.assertIsNotNone(fitted)
        for got, want in zip(fitted, beta):
            self.assertAlmostEqual(got, want, places=6)

    def test_insufficient_points_returns_none(self):
        pairs = synthetic_pairs(method.MIN_FIT_POINTS - 1, [40, 0, 0, 0])
        self.assertIsNone(fit_ols(pairs))

    def test_zero_variance_regressor_dropped(self):
        # 段内降雨恒为 0：应剔除该列而不是失败
        pairs = []
        for i in range(40):
            day = date(2026, 1, 1) + timedelta(days=i)
            temp = 5.0 + (i % 10)
            wind = 1.5 + (i % 5) * 0.3
            value = 50.0 - 0.4 * temp - 3.0 * wind
            pairs.append((make_weather("A", day, temp, wind, 0.0), value))
        fitted = fit_ols(pairs)
        self.assertIsNotNone(fitted)
        self.assertAlmostEqual(fitted[3], 0.0)  # rain 系数为 0
        self.assertAlmostEqual(fitted[1], -0.4, places=6)
        self.assertAlmostEqual(fitted[2], -3.0, places=6)


class MonthlyAdjustmentTests(unittest.TestCase):
    def test_adjustment_removes_weather_effect(self):
        beta = [40.0, 0.0, -5.0, 0.0]  # 风每大 1 m/s，浓度降 5
        segment_pairs = synthetic_pairs(60, beta)
        # 当月静风（相对段内参考），气象效应为正
        month_pairs = [
            (make_weather("A", date(2026, 3, d), 10.0, 1.0, 0.0),
             beta[0] + beta[2] * 1.0)
            for d in range(1, 31)
        ]
        result = monthly_adjustment(month_pairs, segment_pairs)
        self.assertIsNotNone(result)
        self.assertGreater(result.weather_effect, 0)
        # 校正值应接近截距（扣除气象差异后与参考气象对齐）
        ref_wind = sum(r.wind_m_s for r, _ in segment_pairs) / len(segment_pairs)
        self.assertAlmostEqual(result.adjusted_mean, beta[0] + beta[2] * ref_wind, places=4)

    def test_insufficient_fit_returns_none(self):
        month_pairs = [(make_weather("A", date(2026, 3, 1)), 30.0)]
        self.assertIsNone(monthly_adjustment(month_pairs, month_pairs))


if __name__ == "__main__":
    unittest.main()
