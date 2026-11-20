import unittest
from datetime import date

from aqanalysis.trends import (
    analyze_period,
    assess_change,
    city_action_summary,
    match_actions,
)

from helpers import (
    fill_month,
    make_action,
    make_dataset,
    make_obs,
    make_period,
    make_weather,
    month_days_list,
)

NOW = "2026-05-01T00:00:00+00:00"


def two_month_dataset(jan_pm25, feb_pm25, weather_fn=None, stations=None,
                      actions=(), with_weather=True):
    """单站两月数据集。weather_fn(day) -> WeatherRecord。"""
    stations = stations or [make_period(end=None)]
    observations, weather = [], []
    for month, value in ((1, jan_pm25), (2, feb_pm25)):
        obs, wx = fill_month("S1", 2026, month, value,
                             weather=weather_fn if with_weather else None)
        observations += obs
        if with_weather:
            weather += wx
    return make_dataset(stations, observations, weather, actions)


def attributions(dataset, period="2026-02"):
    analysis = analyze_period(dataset, period, NOW)
    return analysis["stations"]["S1"]["change"]["attribution"], analysis


class AttributionTreeTests(unittest.TestCase):
    def test_no_previous(self):
        dataset = two_month_dataset(40.0, 35.0)
        analysis = analyze_period(dataset, "2026-01", NOW)
        # 上一期（2025-12）尚无仪器有效期配置 -> 无环比对象
        attr = analysis["stations"]["S1"]["change"]["attribution"]
        self.assertEqual(attr, "no_previous")

    def test_real_change_detected_beyond_weather(self):
        # 气象恒定，排放下降 10 -> 扣除气象后仍是显著变化
        dataset = two_month_dataset(50.0, 40.0)
        attr, analysis = attributions(dataset)
        self.assertEqual(attr, "real_change")
        change = analysis["stations"]["S1"]["change"]
        self.assertAlmostEqual(change["adjusted_change"], -10.0, places=1)

    def test_weather_explained_change_not_credited(self):
        # 排放恒定，2 月风大 -> 原始下降全由气象解释
        def weather_fn(day):
            wind = 1.0 if day.month == 1 else 3.0
            return make_weather("A", day, temp=10.0, wind=wind)

        def pm25(day):
            return None  # 占位，下面手工构造

        observations, weather = [], []
        for month in (1, 2):
            for day in month_days_list(2026, month):
                wind = 1.0 if month == 1 else 3.0
                observations.append(make_obs("S1", day, 50.0 - 5.0 * wind))
                weather.append(make_weather("A", day, temp=10.0, wind=wind))
        dataset = make_dataset([make_period(end=None)], observations, weather)
        attr, analysis = attributions(dataset)
        self.assertEqual(attr, "met_explained")
        change = analysis["stations"]["S1"]["change"]
        self.assertLess(abs(change["adjusted_change"]), 2.0)
        # 实测与校正结果必须同时可见
        station = analysis["stations"]["S1"]
        self.assertIsNotNone(station["raw_mean"])
        self.assertIsNotNone(station["adjustment"])

    def test_insufficient_coverage_blocks_attribution(self):
        skip = set(month_days_list(2026, 2)[10:])  # 2 月缺测 18 天
        observations, weather = [], []
        for month, value in ((1, 50.0), (2, 30.0)):
            obs, wx = fill_month("S1", 2026, month, value,
                                 skip=skip if month == 2 else ())
            observations += obs
            weather += wx
        dataset = make_dataset([make_period(end=None)], observations, weather)
        attr, analysis = attributions(dataset)
        self.assertEqual(attr, "insufficient_data")
        # 缺测不得当作达标
        self.assertEqual(analysis["stations"]["S1"]["compliance"], "undetermined")

    def test_missing_weather_blocks_deterministic_attribution(self):
        # 观测覆盖充足但无气象资料：显著变化不得给出确定性归因
        dataset = two_month_dataset(50.0, 30.0, with_weather=False)
        attr, analysis = attributions(dataset)
        self.assertEqual(attr, "met_unavailable")
        self.assertIsNone(analysis["stations"]["S1"]["adjustment"])

    def test_instrument_change_breaks_series(self):
        stations = [
            make_period(instrument="I1", start="2026-01-01", end="2026-01-31"),
            make_period(instrument="I2", start="2026-02-01", end=None,
                        event="instrument_change"),
        ]
        dataset = two_month_dataset(50.0, 30.0, stations=stations)
        attr, analysis = attributions(dataset)
        self.assertEqual(attr, "boundary_confounded")
        change = analysis["stations"]["S1"]["change"]
        self.assertTrue(change["crosses_boundary"])
        # 变化量仅供参考，不得作为连续序列结论
        self.assertIsNone(change["adjusted_change"])

    def test_relocation_breaks_series(self):
        stations = [
            make_period(instrument="I1", start="2026-01-01", end="2026-01-31"),
            make_period(instrument="I1", start="2026-02-01", end=None,
                        event="relocation"),
        ]
        dataset = two_month_dataset(50.0, 30.0, stations=stations)
        attr, _ = attributions(dataset)
        self.assertEqual(attr, "boundary_confounded")

    def test_small_change_not_significant(self):
        dataset = two_month_dataset(40.0, 38.0)
        attr, _ = attributions(dataset)
        self.assertEqual(attr, "no_significant_change")


class AreaAggregationTests(unittest.TestCase):
    def test_insufficient_station_excluded_from_area(self):
        stations = [
            make_period(station="S1", area="A", end=None),
            make_period(station="S2", area="A", end=None),
        ]
        observations, weather = [], []
        for month in (1, 2):
            obs, wx = fill_month("S1", 2026, month, 40.0)
            observations += obs
            weather += wx
            skip = set(month_days_list(2026, month)[5:])
            obs2, wx2 = fill_month("S2", 2026, month, 30.0, skip=skip)
            observations += obs2
        dataset = make_dataset(stations, observations, weather)
        analysis = analyze_period(dataset, "2026-02", NOW)
        area = analysis["areas"]["A"]
        self.assertEqual(area["members"], ["S1"])
        self.assertEqual(area["excluded"], ["S2"])
        self.assertFalse(area["sufficient"])  # 区域整体覆盖不足


class ActionMatchingTests(unittest.TestCase):
    def test_shared_action_scope_noted(self):
        actions = [make_action("A9", areas=("North", "East"),
                               start="2026-01-15", end="2026-02-15")]
        matched = match_actions("North", date(2026, 1, 1), date(2026, 2, 28), actions)
        self.assertEqual(len(matched), 1)
        self.assertTrue(matched[0]["shared"])
        self.assertIn("North", matched[0]["scope_note"])
        self.assertIn("East", matched[0]["scope_note"])

    def test_action_outside_window_not_matched(self):
        actions = [make_action("A9", start="2026-05-01", end="2026-05-31")]
        matched = match_actions("North", date(2026, 1, 1), date(2026, 2, 28), actions)
        self.assertEqual(matched, [])

    def test_city_rollup_dedupes_shared_action(self):
        matches = {
            "North": [{"action_id": "A9", "action_type": "dust",
                       "areas": ["North", "East"], "shared": True}],
            "East": [{"action_id": "A9", "action_type": "dust",
                      "areas": ["North", "East"], "shared": True}],
        }
        summary = city_action_summary(matches)
        self.assertEqual(len(summary), 1)  # 同一行动只计一次
        self.assertEqual(sorted(summary[0]["credited_areas"]), ["East", "North"])
        self.assertTrue(summary[0]["dedupe_note"])


if __name__ == "__main__":
    unittest.main()
