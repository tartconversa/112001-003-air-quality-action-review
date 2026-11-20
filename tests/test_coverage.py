import unittest
from datetime import date

from aqanalysis import method
from aqanalysis.coverage import (
    assess_month,
    compliance_status,
    is_valid_day,
)
from aqanalysis.segments import build_segments, segment_for
from aqanalysis.store import resolve_observations

from helpers import make_obs, make_period


def resolved_from(observations):
    return resolve_observations(observations)


class ValidDayTests(unittest.TestCase):
    def test_missing_is_not_valid(self):
        self.assertFalse(is_valid_day(None))
        self.assertFalse(is_valid_day(make_obs("S", "2026-01-01", None, hours=0, flag="missing")))

    def test_low_hours_not_valid(self):
        self.assertFalse(is_valid_day(make_obs("S", "2026-01-01", 30.0, hours=12)))

    def test_suspect_flag_not_valid(self):
        self.assertFalse(is_valid_day(make_obs("S", "2026-01-01", 30.0, flag="suspect")))


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.segments = build_segments([make_period(end=None)])
        self.segment = segment_for(self.segments, "S1", date(2026, 1, 15))

    def test_full_coverage(self):
        observations = [
            make_obs("S1", date(2026, 1, d), 30.0) for d in range(1, 32)
        ]
        coverage = assess_month("S1", 2026, 1, resolved_from(observations), self.segment)
        self.assertEqual(coverage.level, "sufficient")
        self.assertEqual(coverage.valid_days, 31)

    def test_missing_days_reduce_coverage(self):
        observations = [
            make_obs("S1", date(2026, 1, d), 30.0) for d in range(1, 22)
        ] + [
            make_obs("S1", date(2026, 1, d), None, hours=0, flag="missing")
            for d in range(22, 32)
        ]
        coverage = assess_month("S1", 2026, 1, resolved_from(observations), self.segment)
        self.assertEqual(coverage.missing_days, 10)
        self.assertEqual(coverage.level, "limited")  # 21/31 ≈ 0.68

    def test_sparse_coverage_insufficient(self):
        observations = [make_obs("S1", date(2026, 1, d), 30.0) for d in range(1, 8)]
        coverage = assess_month("S1", 2026, 1, resolved_from(observations), self.segment)
        self.assertEqual(coverage.level, "insufficient")

    def test_segment_bounds_limit_expected_days(self):
        segments = build_segments([make_period(start="2026-01-20", end="2026-01-31")])
        segment = segment_for(segments, "S1", date(2026, 1, 25))
        observations = [make_obs("S1", date(2026, 1, d), 30.0) for d in range(20, 32)]
        coverage = assess_month("S1", 2026, 1, resolved_from(observations), segment)
        self.assertEqual(coverage.expected_days, 12)
        self.assertEqual(coverage.level, "sufficient")


class ComplianceTests(unittest.TestCase):
    """缺测不得当作达标：覆盖不足时不得给出达标结论。"""

    def _coverage(self, level):
        from aqanalysis.coverage import MonthCoverage
        return MonthCoverage("S1", "2026-01", 31, 31, 0, 1.0, level)

    def test_missing_not_treated_as_compliant(self):
        # 即使仅有的有效观测均值很低，覆盖不足也不能判达标
        self.assertEqual(
            compliance_status(10.0, self._coverage("insufficient")), "undetermined")
        self.assertEqual(
            compliance_status(10.0, self._coverage("limited")), "undetermined")

    def test_sufficient_coverage_judged_by_limit(self):
        self.assertEqual(
            compliance_status(20.0, self._coverage("sufficient")), "compliant")
        self.assertEqual(
            compliance_status(50.0, self._coverage("sufficient")), "non_compliant")

    def test_limit_boundary(self):
        self.assertEqual(
            compliance_status(method.COMPLIANCE_LIMIT_UG, self._coverage("sufficient")),
            "compliant",
        )


if __name__ == "__main__":
    unittest.main()
