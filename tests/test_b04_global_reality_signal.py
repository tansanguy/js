from __future__ import annotations

import importlib.util
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = PROJECT_ROOT / "09 Compact Corridor Baseline/b04_global_reality_signal_pipeline.py"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("b04_global_reality_signal_test", PIPELINE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class B04GlobalRealitySignalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = load_pipeline()

    def test_tls_phase_states_match_actual_link_indices(self):
        root = ET.fromstring(
            """
            <net>
              <connection from="a" to="b" tl="tls-a" linkIndex="0"/>
              <connection from="c" to="d" tl="tls-a" linkIndex="2"/>
              <connection from="e" to="f" tl="tls-b" linkIndex="1"/>
              <tlLogic id="tls-a" programID="0">
                <phase duration="10" state="GGGrrrr"/>
                <phase duration="3" state="yy"/>
              </tlLogic>
              <tlLogic id="tls-b" programID="0">
                <phase duration="10" state="G"/>
              </tlLogic>
            </net>
            """
        )

        stats = self.pipeline.normalize_tls_phase_state_lengths(root)

        tls_a = root.find("./tlLogic[@id='tls-a']")
        tls_b = root.find("./tlLogic[@id='tls-b']")
        assert tls_a is not None
        assert tls_b is not None
        self.assertEqual([phase.get("state") for phase in tls_a.findall("phase")], ["GGG", "yyr"])
        self.assertEqual([phase.get("state") for phase in tls_b.findall("phase")], ["Gr"])
        self.assertEqual(stats["normalized_phase_state_count"], 3)
        self.assertEqual(stats["normalized_tls_count"], 2)


if __name__ == "__main__":
    unittest.main()
