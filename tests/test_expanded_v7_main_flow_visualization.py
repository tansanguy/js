import importlib.util
import json
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "04-2 Visualize/visualize_expanded_v7_b0_main_flow.py"


def load_visualizer():
    spec = importlib.util.spec_from_file_location("expanded_v7_main_flow_visualizer_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ExpandedV7MainFlowVisualizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.visualizer = load_visualizer()

    def test_visualizer_defaults_are_v7_isolated(self):
        self.assertIn("04-2 Visualize", str(SCRIPT_PATH))
        self.assertTrue(str(self.visualizer.DEFAULT_OUTPUT_DIR).endswith("results/html"))
        self.assertTrue(str(self.visualizer.DEFAULT_MANIFEST).endswith("configs/expanded_v7_b0_manifest.json"))

    def test_generated_main_flow_html_when_available(self):
        html_path = PROJECT_ROOT / "results/html/expanded_v7_b0_main_flow_animation.html"
        json_path = PROJECT_ROOT / "results/html/expanded_v7_b0_main_flow_animation.json"
        if not html_path.is_file() or not json_path.is_file():
            self.skipTest("main-flow visualization has not been generated")
        text = html_path.read_text(encoding="utf-8")
        for needle in ["L.circleMarker", "bgByT", "bgLayer", "updatePanel", "speedColor", "Expanded V7 B0 메인 교통흐름"]:
            self.assertIn(needle, text)
        self.assertNotIn("vehicle-node", text)
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "expanded_v7_b0_main_flow_animation.v1")
        self.assertGreater(len(payload["emergency"]), 0)
        self.assertGreater(len(payload["background"]), 0)
        self.assertGreater(len(payload["mainline_edges"]), 0)
        self.assertIn("mainline_stop_edge_count", payload["metrics"])

    def test_04_visualize_has_no_local_diff(self):
        completed = subprocess.run(
            ["git", "diff", "--", "04_visualize"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
