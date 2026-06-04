#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = PROJECT_ROOT / "09 Compact Corridor Baseline/compact_v9_pipeline.py"
SUMMARY = PROJECT_ROOT / "results/metrics/compact_v9/signal_green/compact_v9_signal_green_candidate_summary.json"
HTML = PROJECT_ROOT / "results/html/compact_v9_signal_green_review.html"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("compact_v9_pipeline_test", PIPELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CompactV9SignalGreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = load_pipeline()

    def test_mainline_green_candidates_pass_and_keep_cycle(self):
        payload = json.loads(SUMMARY.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["default_candidate"], "green18")
        by_name = {candidate["name"]: candidate for candidate in payload["candidates"]}
        self.assertEqual(set(by_name), {"green12", "green18", "green24"})
        for name, expected in {"green12": 12.0, "green18": 18.0, "green24": 24.0}.items():
            candidate = by_name[name]
            self.assertEqual(candidate["status"], "PASS")
            self.assertAlmostEqual(candidate["change"]["before_cycle_sec"], 90.0)
            self.assertAlmostEqual(candidate["change"]["after_cycle_sec"], 90.0)
            self.assertAlmostEqual(candidate["change"]["before_target_green_sec"], 6.0)
            self.assertAlmostEqual(candidate["change"]["after_target_green_sec"], expected)
            self.assertEqual(candidate["sumo_load"]["status"], "PASS")
            self.assertEqual(candidate["signal_integrity"]["status"], "PASS")
            self.assertEqual(candidate["route_connectivity"]["bad_pair_count"], 0)
            self.assertEqual(candidate["firetruck_smoke"]["status"], "PASS")
            self.assertFalse(candidate["firetruck_smoke"]["emergency_teleport"])

    def test_green18_net_has_target_link_green_18(self):
        net_path = PROJECT_ROOT / "data_prepared/compact_v9/net/jungbu_compact_v9_ellipse_lanes_repaired_entry_tls_connected_mainline_green18.net.xml"
        root = ET.parse(net_path).getroot()
        logic = next(item for item in root.findall("tlLogic") if item.get("id") == self.pipeline.MAINLINE_GREEN_TLS_ID)
        green = self.pipeline.tls_link_green_seconds(logic, self.pipeline.MAINLINE_GREEN_LINK_INDEX)
        self.assertAlmostEqual(green, 18.0)
        self.assertAlmostEqual(sum(float(phase.get("duration")) for phase in logic.findall("phase")), 90.0)

    def test_review_html_exists_and_mentions_target(self):
        text = HTML.read_text(encoding="utf-8")
        self.assertIn("Compact V9 S15/S16 Mainline Green", text)
        self.assertIn("781985787#0", text)
        self.assertIn("218915135#3", text)
        self.assertIn("green18", text)


if __name__ == "__main__":
    unittest.main()
