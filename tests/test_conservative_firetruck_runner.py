import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("runner_conservative_test", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ConservativeFiretruckRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def write_route_xml(self, path: Path):
        root = ET.Element("routes")
        ET.SubElement(root, "vType", {"id": "old_type"})
        ET.SubElement(root, "route", {"id": "r0", "edges": "a b"})
        ET.SubElement(root, "vehicle", {"id": "veh0", "type": "old_type", "route": "r0", "depart": "600"})
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)

    def test_aggressive_profile_keeps_legacy_insertion_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "route.rou.xml"
            self.write_route_xml(path)
            self.runner.set_congested_emergency_departure(path, {"id": "firetruck_emergency"}, "")
            root = ET.parse(path).getroot()
            vtype = root.find("vType")
            vehicle = root.find("vehicle")
        self.assertEqual(vtype.get("lcAssertive"), "5.0")
        self.assertEqual(vtype.get("lcCooperative"), "0.0")
        self.assertEqual(vehicle.get("departLane"), "free")
        self.assertEqual(vehicle.get("departSpeed"), "max")
        self.assertEqual(vehicle.get("insertionChecks"), "none")

    def test_conservative_profile_uses_sumo_insertion_checks(self):
        attrs = {
            "id": "firetruck_emergency_conservative_b0",
            "lcAssertive": "1.0",
            "lcCooperative": "0.7",
            "lcStrategic": "3.0",
            "lcSpeedGain": "1.0",
            "speedFactor": "1.05",
            "maxSpeed": f"{60 / 3.6:.6f}",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "route.rou.xml"
            self.write_route_xml(path)
            self.runner.set_congested_emergency_departure(path, attrs, "conservative_firetruck_b0")
            root = ET.parse(path).getroot()
            vtype = root.find("vType")
            vehicle = root.find("vehicle")
        self.assertEqual(vtype.get("id"), "firetruck_emergency_conservative_b0")
        self.assertEqual(vtype.get("lcAssertive"), "1.0")
        self.assertEqual(vtype.get("lcCooperative"), "0.7")
        self.assertEqual(vehicle.get("departLane"), "best")
        self.assertEqual(vehicle.get("departSpeed"), "0")
        self.assertIsNone(vehicle.get("insertionChecks"))

    def test_conservative_profile_disables_dynamic_insert(self):
        task = {
            "mode": "B0",
            "emergency_depart": 600,
            "emergency_behavior_profile": "conservative_firetruck_b0",
        }
        result = self.runner.configure_dynamic_emergency_departure(task, Path("/tmp/not_used.rou.xml"))
        self.assertFalse(result["dynamic_emergency_insert"])


if __name__ == "__main__":
    unittest.main()
