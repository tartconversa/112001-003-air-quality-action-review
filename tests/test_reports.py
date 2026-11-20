import csv
import tempfile
import unittest
from pathlib import Path

from aqanalysis.reports import publish_report, render_markdown
from aqanalysis.review import register_reviews, required_reviews
from aqanalysis.store import Store
from aqanalysis.trends import analyze_period

NOW = "2026-05-01T00:00:00+00:00"

HEADER_OBS = ["station_id", "date", "pm25", "valid_hours", "quality_flag", "obs_version"]
HEADER_STN = ["station_id", "area_id", "instrument", "valid_from", "valid_to", "event"]
HEADER_WX = ["area_id", "date", "temp_c", "wind_m_s", "rain_mm"]
HEADER_ACT = ["action_id", "action_type", "area_scope", "start", "end", "description"]


def write_dataset(directory, obs_rows, version_note=""):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "stations.csv").open("w", newline="", encoding="utf-8") as s:
        csv.writer(s).writerows([HEADER_STN, ["S1", "A", "I1", "2026-01-01", "", ""]])
    with (directory / "observations.csv").open("w", newline="", encoding="utf-8") as s:
        csv.writer(s).writerows([HEADER_OBS] + obs_rows)
    with (directory / "weather.csv").open("w", newline="", encoding="utf-8") as s:
        rows = [HEADER_WX]
        for month in (1, 2):
            for day in range(1, 29):
                rows.append(["A", f"2026-{month:02d}-{day:02d}", 10, 2.0, 0])
        csv.writer(s).writerows(rows)
    with (directory / "actions.csv").open("w", newline="", encoding="utf-8") as s:
        csv.writer(s).writerows(
            [HEADER_ACT, ["A1", "upgrade", "A", "2026-02-01", "2026-02-28", "改造"]])


def jan_feb_rows(jan_value, feb_value, missing_feb_days=(), version=1):
    rows = []
    for day in range(1, 29):
        rows.append(["S1", f"2026-01-{day:02d}", jan_value, 24, "valid", version])
    for day in range(1, 29):
        if day in missing_feb_days:
            rows.append(["S1", f"2026-02-{day:02d}", "", 0, "missing", version])
        else:
            rows.append(["S1", f"2026-02-{day:02d}", feb_value, 24, "valid", version])
    return rows


class ReportFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "state")

    def _ingest(self, rows):
        data_dir = self.root / f"data_{len(list(self.root.glob('data_*')))}"
        write_dataset(data_dir, rows)
        return self.store.ingest(data_dir, now=NOW)

    def _analyze_and_publish(self, period="2026-02"):
        analysis = analyze_period(self.store.dataset(), period, NOW)
        self.store.save_analysis(period, analysis)
        reviews = self.store.load_reviews()
        merged, _ = register_reviews(reviews, required_reviews(analysis, NOW))
        self.store.save_reviews(merged)
        return publish_report(self.store, period, analysis, merged, now=NOW)

    def test_first_publication_snapshot(self):
        self._ingest(jan_feb_rows(50, 40))
        revision = self._analyze_and_publish()
        self.assertEqual(revision["seq"], 1)
        self.assertEqual(revision["obs_version"], 1)
        self.assertEqual(revision["method"]["id"], "aq-trend/v1")
        self.assertEqual(revision["revision_note"], ["首次发布。"])
        # 快照保留发布时的行动状态
        self.assertTrue(any(
            r["reason"].startswith("扣除气象影响") for r in revision["open_reviews"]))

    def test_revision_note_when_conclusions_hold(self):
        self._ingest(jan_feb_rows(48, 46))
        self._analyze_and_publish()
        # 小幅更正一天，不改变任何结论
        self._ingest(jan_feb_rows(48, 46) + [["S1", "2026-01-10", 50.0, 24, "valid", 2]])
        revision = self._analyze_and_publish()
        self.assertEqual(revision["seq"], 2)
        note = " ".join(revision["revision_note"])
        self.assertIn("结论未变", note)
        self.assertIn("v1 -> v2", note)

    def test_revision_note_when_backfill_changes_conclusions(self):
        # v1：2 月缺测 10 天 -> 覆盖不足不作归因
        self._ingest(jan_feb_rows(48, 46, missing_feb_days=set(range(1, 11))))
        first = self._analyze_and_publish()
        self.assertEqual(
            first["conclusions"]["station:S1"]["attribution"], "insufficient_data")
        # v2：补录缺测日 -> 覆盖充足，结论变化
        backfill = [["S1", f"2026-02-{d:02d}", 46.0, 24, "valid", 2]
                    for d in range(1, 11)]
        self._ingest(backfill)
        revision = self._analyze_and_publish()
        note = " ".join(revision["revision_note"])
        self.assertIn("结论变化", note)
        self.assertIn("补录/更正", note)
        self.assertEqual(
            revision["conclusions"]["station:S1"]["coverage"], "sufficient")

    def test_history_revisions_not_rewritten(self):
        self._ingest(jan_feb_rows(48, 46, missing_feb_days=set(range(1, 11))))
        self._analyze_and_publish()
        backfill = [["S1", f"2026-02-{d:02d}", 46.0, 24, "valid", 2]
                    for d in range(1, 11)]
        self._ingest(backfill)
        self._analyze_and_publish()
        report = self.store.load_report("2026-02")
        self.assertEqual(len(report["revisions"]), 2)
        first, second = report["revisions"]
        # 第一版仍保留发布时的观测版本与结论
        self.assertEqual(first["obs_version"], 1)
        self.assertEqual(
            first["conclusions"]["station:S1"]["attribution"], "insufficient_data")
        self.assertEqual(second["obs_version"], 2)

    def test_markdown_shows_raw_and_adjusted_side_by_side(self):
        self._ingest(jan_feb_rows(50, 40))
        revision = self._analyze_and_publish()
        markdown = render_markdown("2026-02", revision)
        self.assertIn("实测均值", markdown)
        self.assertIn("气象校正均值", markdown)
        self.assertIn("观测版本：v1", markdown)
        self.assertIn("跟进期限", markdown)


if __name__ == "__main__":
    unittest.main()
