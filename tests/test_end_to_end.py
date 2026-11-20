"""端到端：用随附合成数据走完整流程（导入 -> 分析 -> 发布 -> 补录 -> 再评估 -> 修订）。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from aqanalysis.cli import main

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def run_cli(*argv):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = main(list(argv))
    return code, out.getvalue()


class StateCase(unittest.TestCase):
    """每个子类拥有独立状态目录。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.state = str(Path(cls.tmp.name) / "state")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def analysis(self, period):
        with open(Path(self.state) / "analyses" / f"{period}.json",
                  encoding="utf-8") as stream:
            return json.load(stream)

    def ingest_analyze_publish(self, periods=("2026-02", "2026-03", "2026-04")):
        run_cli("--state-dir", self.state, "ingest", "--data-dir", str(DATA))
        run_cli("--state-dir", self.state, "analyze", "--all")
        for period in periods:
            run_cli("--state-dir", self.state, "report", "--period", period)


class V1NarrativeTests(StateCase):
    """v1 数据下的归因叙事。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ingest_analyze_publish(cls)

    def test_boiler_upgrade_is_real_change(self):
        feb = self.analysis("2026-02")
        # 锅炉改造：扣除气象后仍显著改善
        self.assertEqual(
            feb["stations"]["S01"]["change"]["attribution"], "real_change")

    def test_relocation_not_silently_joined(self):
        mar = self.analysis("2026-03")
        self.assertEqual(
            mar["stations"]["S03"]["change"]["attribution"], "boundary_confounded")

    def test_missing_not_treated_as_compliant(self):
        mar = self.analysis("2026-03")
        self.assertEqual(mar["stations"]["S02"]["compliance"], "undetermined")
        self.assertEqual(mar["stations"]["S02"]["coverage"]["level"], "limited")

    def test_instrument_change_not_silently_joined(self):
        apr = self.analysis("2026-04")
        self.assertEqual(
            apr["stations"]["S01"]["change"]["attribution"], "boundary_confounded")

    def test_gap_blocks_april_attribution(self):
        apr = self.analysis("2026-04")
        self.assertEqual(
            apr["stations"]["S02"]["change"]["attribution"], "insufficient_data")

    def test_raw_and_adjusted_both_visible(self):
        apr = self.analysis("2026-04")
        self.assertIsNotNone(apr["stations"]["S02"]["raw_mean"])
        self.assertIsNotNone(apr["stations"]["S02"]["adjustment"])

    def test_shared_action_counted_once(self):
        apr = self.analysis("2026-04")
        a02 = [a for a in apr["city_actions"] if a["action_id"] == "A02"]
        self.assertEqual(len(a02), 1)
        self.assertEqual(sorted(a02[0]["credited_areas"]), ["East", "North"])
        # 各区域结果中均标注适用范围
        for area in ("North", "East"):
            matched = apr["areas"][area]["matched_actions"]
            note = [m["scope_note"] for m in matched if m["action_id"] == "A02"][0]
            self.assertIn("不得在各区域重复记功", note)


class BackfillFlowTests(StateCase):
    """补录/更正后的再评估与修订。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ingest_analyze_publish(cls)
        # 补录/更正（v2），随后重新分析与发布
        _, cls.ingest_out = run_cli("--state-dir", cls.state, "ingest",
                                    "--data-dir", str(DATA / "backfill"))
        _, cls.status_out = run_cli("--state-dir", cls.state, "status")
        run_cli("--state-dir", cls.state, "analyze", "--all")
        _, cls.report_apr = run_cli("--state-dir", cls.state, "report",
                                    "--period", "2026-04")
        _, cls.report_feb = run_cli("--state-dir", cls.state, "report",
                                    "--period", "2026-02")

    def test_ingest_reports_changes(self):
        self.assertIn("v2", self.ingest_out)
        self.assertIn("9 条", self.ingest_out)

    def test_status_flags_stale_analyses_and_reviews(self):
        self.assertIn("需重新评估", self.status_out)

    def test_april_change_now_met_explained(self):
        apr = self.analysis("2026-04")
        self.assertEqual(
            apr["stations"]["S02"]["change"]["attribution"], "met_explained")

    def test_revision_note_explains_change(self):
        self.assertIn("结论变化", self.report_apr)

    def test_revision_note_explains_no_change(self):
        self.assertIn("结论未变", self.report_feb)

    def test_history_revisions_preserved(self):
        with open(Path(self.state) / "reports" / "2026-04.json",
                  encoding="utf-8") as stream:
            report = json.load(stream)
        self.assertEqual(report["revisions"][0]["obs_version"], 1)
        self.assertEqual(report["revisions"][-1]["obs_version"], 2)
        # 第一版仍保留当时的归因结论
        self.assertEqual(
            report["revisions"][0]["conclusions"]["station:S02"]["attribution"],
            "insufficient_data",
        )


if __name__ == "__main__":
    unittest.main()
