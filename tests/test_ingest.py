import csv
import tempfile
import unittest
from pathlib import Path

from aqanalysis.ingest import (
    IngestionError,
    load_actions,
    load_observations,
    load_stations,
)
from aqanalysis.store import diff_resolved, resolve_observations

from helpers import make_obs, make_period


def write_csv(directory, name, rows):
    path = Path(directory) / name
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerows(rows)
    return path


class LoadStationsTests(unittest.TestCase):
    def test_valid_periods(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(tmp, "stations.csv", [
                ["station_id", "area_id", "instrument", "valid_from", "valid_to", "event"],
                ["S1", "A", "I1", "2026-01-01", "2026-03-31", ""],
                ["S1", "A", "I2", "2026-04-01", "", "instrument_change"],
            ])
            periods = load_stations(path)
        self.assertEqual(len(periods), 2)
        self.assertIsNone(periods[1].valid_to)
        self.assertEqual(periods[1].event, "instrument_change")

    def test_overlapping_periods_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(tmp, "stations.csv", [
                ["station_id", "area_id", "instrument", "valid_from", "valid_to"],
                ["S1", "A", "I1", "2026-01-01", "2026-04-15"],
                ["S1", "A", "I2", "2026-04-01", ""],
            ])
            with self.assertRaises(IngestionError):
                load_stations(path)

    def test_missing_column_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(tmp, "stations.csv", [
                ["station_id", "area_id"],
                ["S1", "A"],
            ])
            with self.assertRaises(IngestionError):
                load_stations(path)


class LoadObservationsTests(unittest.TestCase):
    def test_empty_pm25_becomes_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(tmp, "observations.csv", [
                ["station_id", "date", "pm25", "valid_hours", "quality_flag", "obs_version"],
                ["S1", "2026-01-01", "", 0, "missing", 1],
                ["S1", "2026-01-02", "31.5", 24, "valid", 1],
            ])
            observations = load_observations(path)
        self.assertIsNone(observations[0].pm25)
        self.assertEqual(observations[1].pm25, 31.5)

    def test_invalid_hours_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(tmp, "observations.csv", [
                ["station_id", "date", "pm25", "valid_hours", "quality_flag", "obs_version"],
                ["S1", "2026-01-01", "30", 25, "valid", 1],
            ])
            with self.assertRaises(IngestionError):
                load_observations(path)


class LoadActionsTests(unittest.TestCase):
    def test_multi_area_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(tmp, "actions.csv", [
                ["action_id", "action_type", "area_scope", "start", "end", "description"],
                ["A1", "dust", "North;East", "2026-04-01", "2026-04-30", "联合治理"],
            ])
            actions = load_actions(path)
        self.assertEqual(actions[0].areas, ("North", "East"))
        self.assertTrue(actions[0].shared)

    def test_duplicate_action_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(tmp, "actions.csv", [
                ["action_id", "action_type", "area_scope", "start", "end"],
                ["A1", "x", "North", "2026-04-01", "2026-04-30"],
                ["A1", "y", "South", "2026-04-01", "2026-04-30"],
            ])
            with self.assertRaises(IngestionError):
                load_actions(path)


class VersionResolutionTests(unittest.TestCase):
    def test_latest_version_wins(self):
        observations = [
            make_obs("S1", "2026-01-01", 30.0, version=1),
            make_obs("S1", "2026-01-01", 25.0, version=2),
            make_obs("S1", "2026-01-02", 40.0, version=1),
        ]
        resolved = resolve_observations(observations)
        from datetime import date
        self.assertEqual(resolved[("S1", date(2026, 1, 1))].pm25, 25.0)
        self.assertEqual(resolved[("S1", date(2026, 1, 2))].pm25, 40.0)

    def test_diff_detects_backfill_and_correction(self):
        old = resolve_observations([
            make_obs("S1", "2026-01-01", None, hours=0, flag="missing"),
            make_obs("S1", "2026-01-02", 40.0),
        ])
        new = resolve_observations([
            make_obs("S1", "2026-01-01", None, hours=0, flag="missing"),
            make_obs("S1", "2026-01-01", 32.0, version=2),   # 补录
            make_obs("S1", "2026-01-02", 40.0),
            make_obs("S1", "2026-01-02", 38.0, version=2),   # 更正
        ])
        changed = set(diff_resolved(old, new))
        from datetime import date
        self.assertEqual(
            changed,
            {("S1", date(2026, 1, 1)), ("S1", date(2026, 1, 2))},
        )


if __name__ == "__main__":
    unittest.main()
