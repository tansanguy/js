from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANIMATE_PATH = PROJECT_ROOT / "04-3 Visualize/animate_b04_b4_destination.py"
VISUALIZE_041_DIR = PROJECT_ROOT / "04-1 Visualize"

if str(VISUALIZE_041_DIR) not in sys.path:
    sys.path.insert(0, str(VISUALIZE_041_DIR))

spec = importlib.util.spec_from_file_location("b04_b4_destination_animation_under_test", ANIMATE_PATH)
assert spec and spec.loader
animation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = animation
spec.loader.exec_module(animation)

from utils.trajectory_parser import EmergencyTrajectory, TrajectoryPoint  # noqa: E402


class B04B4DestinationAnimationTest(unittest.TestCase):
    def test_background_payload_preserves_vehicle_identity_and_lane(self) -> None:
        traj = EmergencyTrajectory("B4", "theta", "001")
        traj.add_point(
            TrajectoryPoint(
                time=600.0,
                edge_id="edge-a",
                lat=37.55,
                lon=126.98,
                speed_kmh=10.0,
                angle=90.0,
                lane_id="edge-a_0",
                lane_pos_m=1.0,
            )
        )
        fcd = animation.FcdResult(
            emergency=traj,
            emergency_id="emergency_0",
            mode="B4",
            background=[
                {
                    "time": 600.0,
                    "vehicles": [
                        {
                            "id": "veh_001",
                            "lat": 37.5501,
                            "lon": 126.9801,
                            "speed_kmh": 12.0,
                            "angle": 91.0,
                            "edge": "edge-bg",
                            "lane": "edge-bg_1",
                        }
                    ],
                }
            ],
        )

        payload = animation.build_mode_payload(
            mode="B4",
            fcd=fcd,
            tripinfo={"duration": "1", "arrival": "-1", "arrivalLane": ""},
            bg_radius_m=250.0,
            route_geometry={"coords": [], "path": [], "edge_measures": {}, "length_m": 0.0},
            planned_edges=[],
        )

        vehicle = payload["background"][0]["vehicles"][0]
        self.assertEqual(vehicle["id"], "veh_001")
        self.assertEqual(vehicle["edge"], "edge-bg")
        self.assertEqual(vehicle["lane"], "edge-bg_1")


if __name__ == "__main__":
    unittest.main()
