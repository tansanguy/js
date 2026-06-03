from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VISUALIZE_041_DIR = PROJECT_ROOT / "04-1 Visualize"
ANIMATE_PATH = VISUALIZE_041_DIR / "animate_custom_destination_b0.py"

if str(VISUALIZE_041_DIR) not in sys.path:
    sys.path.insert(0, str(VISUALIZE_041_DIR))

spec = importlib.util.spec_from_file_location("custom_destination_b0_animation_under_test", ANIMATE_PATH)
assert spec and spec.loader
custom_animation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = custom_animation
spec.loader.exec_module(custom_animation)

from utils.trajectory_parser import EmergencyTrajectory, TrajectoryPoint  # noqa: E402


class CustomDestinationB0AnimationTest(unittest.TestCase):
    def test_loads_only_two_custom_b0_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            results_csv = tmp / "experiment_results.csv"
            with results_csv.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=["mode", "parameter_id", "route_id", "run_dir", "emergency_travel_time_sec"],
                )
                writer.writeheader()
                writer.writerow({"mode": "B0", "parameter_id": "no_control", "route_id": "CUSTOM_A", "run_dir": "a"})
                writer.writerow({"mode": "B0", "parameter_id": "no_control", "route_id": "CUSTOM_B", "run_dir": "b"})
                writer.writerow({"mode": "B2", "parameter_id": "no_control", "route_id": "CUSTOM_A", "run_dir": "c"})
                writer.writerow({"mode": "B0", "parameter_id": "theta", "route_id": "CUSTOM_A", "run_dir": "d"})
                writer.writerow({"mode": "B0", "parameter_id": "no_control", "route_id": "SEOUL_STATION", "run_dir": "e"})
            latest = tmp / "latest.json"
            latest.write_text(json.dumps({"results_csv": str(results_csv)}), encoding="utf-8")

            _payload, rows = custom_animation.load_custom_b0_results(latest)

        self.assertEqual([row["route_id"] for row in rows], ["CUSTOM_A", "CUSTOM_B"])

    def test_payload_uses_accepted_route_length_not_seoul_station_constant(self) -> None:
        traj = EmergencyTrajectory("B0", "no_control", "001")
        traj.add_point(TrajectoryPoint(time=600.0, edge_id="edge-a", lat=37.55, lon=126.98, speed_kmh=10.0))
        traj.add_point(TrajectoryPoint(time=601.0, edge_id="edge-b", lat=37.551, lon=126.981, speed_kmh=20.0))
        fcd = custom_animation.FcdResult(
            emergency=traj,
            emergency_id="emergency_B0_001",
            mode="B0",
            background=[{"time": 600.0, "vehicles": [{"lat": 37.5501, "lon": 126.9801, "speed_kmh": 12.0, "angle": 0.0}]}],
        )
        route_length = 2335.78

        payload = custom_animation.build_b0_payload(
            fcd,
            route_length_m=route_length,
            bg_radius_m=250,
            result_row={
                "route_id": "CUSTOM_JUNG_GU_PILDONG2_84_101",
                "emergency_travel_time_sec": "259",
                "final_status": "PASS",
            },
            route_row={
                "route_id": "CUSTOM_JUNG_GU_PILDONG2_84_101",
                "label_ko": "필동2가 84-101",
                "target_edge_id": "-273640070#3",
                "route_length_m": str(route_length),
                "lat": "37.556682",
                "lon": "126.993665",
            },
        )

        self.assertEqual(payload["distance_m"], route_length)
        self.assertEqual(payload["emergency"][-1]["dist_m"], route_length)
        self.assertNotEqual(payload["distance_m"], 2990.17)
        self.assertEqual(len(payload["background"]), 1)

    def test_generated_html_uses_04_circle_marker_flow(self) -> None:
        doc = {
            "schema": "custom_destination_b0_animation.v1",
            "meta": {"route_length_m": 100.0},
            "modes": {
                "B0": {
                    "route_id": "CUSTOM_TEST",
                    "label_ko": "테스트",
                    "travel_time_sec": 2.0,
                    "depart_time_sec": 600.0,
                    "route_polyline": [[37.55, 126.98], [37.551, 126.981]],
                    "emergency": [
                        {"t_rel": 0.0, "lat": 37.55, "lon": 126.98, "speed_kmh": 10.0, "dist_m": 0.0},
                        {"t_rel": 2.0, "lat": 37.551, "lon": 126.981, "speed_kmh": 20.0, "dist_m": 100.0},
                    ],
                    "background": [
                        {
                            "t_rel": 0.0,
                            "vehicles": [{"lat": 37.5501, "lon": 126.9801, "speed_kmh": 12.0, "angle": 0.0}],
                        }
                    ],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "animation.html"
            custom_animation.build_animated_single_map_html(doc, output, "Custom B0")
            html = output.read_text(encoding="utf-8")

        for needle in ["L.circleMarker", "bgByT", "bgLayer", "updatePanel", "preferCanvas:true"]:
            self.assertIn(needle, html)
        for banned in ["vehicle-node", "divIcon", "119"]:
            self.assertNotIn(banned, html)

    def test_04_visualize_has_no_diff(self) -> None:
        result = subprocess.run(
            ["git", "diff", "--quiet", "--", "04_visualize"],
            cwd=PROJECT_ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
