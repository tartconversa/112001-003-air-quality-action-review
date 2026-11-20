import unittest
from datetime import date

from aqanalysis.segments import build_segments, same_segment, segment_for

from helpers import make_period


class SegmentTests(unittest.TestCase):
    def test_instrument_change_splits_series(self):
        segments = build_segments([
            make_period(instrument="I1", start="2026-01-01", end="2026-03-31"),
            make_period(instrument="I2", start="2026-04-01", end=None,
                        event="instrument_change"),
        ])
        march = segment_for(segments, "S1", date(2026, 3, 15))
        april = segment_for(segments, "S1", date(2026, 4, 15))
        self.assertEqual(march.instrument, "I1")
        self.assertEqual(april.instrument, "I2")
        self.assertFalse(same_segment(march, april))
        self.assertTrue(same_segment(march, march))

    def test_relocation_event_recorded(self):
        segments = build_segments([
            make_period(instrument="I1", start="2026-01-01", end="2026-02-28"),
            make_period(instrument="I1b", start="2026-03-01", end=None,
                        event="relocation"),
        ])
        march = segment_for(segments, "S1", date(2026, 3, 1))
        self.assertEqual(march.event, "relocation")

    def test_date_outside_config(self):
        segments = build_segments([
            make_period(start="2026-02-01", end="2026-02-28"),
        ])
        self.assertIsNone(segment_for(segments, "S1", date(2026, 1, 15)))


if __name__ == "__main__":
    unittest.main()
