import unittest

from aqanalysis.models import ReviewAction
from aqanalysis.review import (
    analysis_staleness,
    close_review,
    flag_reevaluation,
    register_reviews,
    required_reviews,
)

NOW = "2026-05-01T00:00:00+00:00"


def make_analysis(attribution="real_change", coverage="sufficient",
                  raw_change=-8.0, crosses=False, period="2026-02",
                  obs_version=1):
    return {
        "period": period,
        "obs_version": obs_version,
        "stations": {
            "S1": {
                "area_id": "A",
                "coverage": {"level": coverage},
                "change": {
                    "attribution": attribution,
                    "raw_change": raw_change,
                    "crosses_boundary": crosses,
                },
            }
        },
    }


class RequiredReviewsTests(unittest.TestCase):
    def test_real_change_creates_review_with_area_and_due_date(self):
        reviews = required_reviews(make_analysis(), NOW)
        self.assertEqual(len(reviews), 1)
        review = reviews[0]
        self.assertEqual(review.area_id, "A")
        self.assertEqual(review.station_id, "S1")
        self.assertEqual(review.period, "2026-02")
        self.assertTrue(review.due_date > NOW[:10])  # 有跟进期限
        self.assertEqual(review.status, "open")

    def test_coverage_gap_creates_review(self):
        reviews = required_reviews(make_analysis(
            attribution="no_significant_change", coverage="limited"), NOW)
        self.assertEqual(len(reviews), 1)
        self.assertIn("覆盖不足", reviews[0].reason)

    def test_boundary_with_significant_raw_change_creates_review(self):
        reviews = required_reviews(make_analysis(
            attribution="boundary_confounded", crosses=True), NOW)
        self.assertEqual(len(reviews), 1)
        self.assertIn("边界", reviews[0].reason)

    def test_quiet_month_creates_nothing(self):
        reviews = required_reviews(make_analysis(
            attribution="no_significant_change", raw_change=-1.0), NOW)
        self.assertEqual(reviews, [])

    def test_registration_dedupes(self):
        required = required_reviews(make_analysis(), NOW)
        merged, added1 = register_reviews([], required)
        merged, added2 = register_reviews(merged, required)
        self.assertEqual(len(added1), 1)
        self.assertEqual(added2, [])
        self.assertEqual(len(merged), 1)


class ReevaluationTests(unittest.TestCase):
    def _review(self, period="2026-03", created_version=1):
        return ReviewAction(
            review_id="R1", station_id="S1", area_id="A", period=period,
            reason="测试", created_at=NOW, created_obs_version=created_version,
            due_date="2026-06-01",
        )

    def test_backfill_flags_open_review(self):
        review = self._review()
        log = [{
            "obs_version": 2,
            "changed": [{"station_id": "S1", "date": "2026-03-10"}],
        }]
        flag_reevaluation([review], log)
        self.assertTrue(review.needs_reevaluation)

    def test_previous_month_change_flags_review(self):
        # 3 月复核依赖 2 月环比数据：2 月更正同样触发再评估
        review = self._review(period="2026-03")
        log = [{
            "obs_version": 2,
            "changed": [{"station_id": "S1", "date": "2026-02-14"}],
        }]
        flag_reevaluation([review], log)
        self.assertTrue(review.needs_reevaluation)

    def test_closed_review_not_flagged(self):
        review = self._review()
        review.status = "closed"
        log = [{
            "obs_version": 2,
            "changed": [{"station_id": "S1", "date": "2026-03-10"}],
        }]
        flag_reevaluation([review], log)
        self.assertFalse(review.needs_reevaluation)

    def test_older_version_change_ignored(self):
        review = self._review(created_version=2)
        log = [{
            "obs_version": 2,
            "changed": [{"station_id": "S1", "date": "2026-03-10"}],
        }]
        flag_reevaluation([review], log)
        self.assertFalse(review.needs_reevaluation)

    def test_close_review(self):
        review = self._review()
        closed = close_review([review], "R1", NOW)
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.closed_at, NOW)
        with self.assertRaises(KeyError):
            close_review([review], "R1", NOW)


class StalenessTests(unittest.TestCase):
    def test_analysis_stale_when_inputs_change(self):
        analysis = {"period": "2026-04", "obs_version": 1,
                    "stations": {"S1": {}, "S2": {}}}
        log = [{
            "obs_version": 2,
            "changed": [{"station_id": "S2", "date": "2026-03-10"}],  # 上一期也算
        }]
        stale, reasons = analysis_staleness(analysis, log)
        self.assertTrue(stale)
        self.assertIn("S2", reasons[0])

    def test_analysis_fresh_when_changes_elsewhere(self):
        analysis = {"period": "2026-04", "obs_version": 1,
                    "stations": {"S1": {}}}
        log = [{
            "obs_version": 2,
            "changed": [{"station_id": "S9", "date": "2026-04-10"}],
        }]
        stale, _ = analysis_staleness(analysis, log)
        self.assertFalse(stale)


if __name__ == "__main__":
    unittest.main()
