from __future__ import annotations

import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = PROJECT_ROOT / "01-1 Validation/validate_b0_reality_recall.py"

spec = importlib.util.spec_from_file_location("validate_b0_reality_recall", VALIDATOR_PATH)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validator
spec.loader.exec_module(validator)


class B0RealityValidationMathTest(unittest.TestCase):
    def test_scaled_reference_seconds_uses_warmup_and_sustain_windows(self) -> None:
        scaled = validator.scaled_reference_seconds(
            begin=0.0,
            end=2373.0,
            warmup_sec=600.0,
            warmup_scale=0.15,
            sustain_scale=0.05,
        )

        self.assertAlmostEqual(scaled, 600.0 * 0.15 + (2373.0 - 600.0) * 0.05)

    def test_geh_statistic_and_status(self) -> None:
        self.assertEqual(validator.geh_statistic(0.0, 0.0), 0.0)
        self.assertEqual(validator.geh_status(4.99), "PASS")
        self.assertEqual(validator.geh_status(5.0), "WARN")
        self.assertEqual(validator.geh_status(10.0), "FAIL")

    def test_edge_speed_status_flags_over_open_edges(self) -> None:
        self.assertEqual(validator.edge_speed_row_status(8.1), ("FAIL", "over_open_speed"))
        self.assertEqual(validator.edge_speed_row_status(-8.1), ("FAIL", "under_speed"))
        self.assertEqual(validator.edge_speed_row_status(6.0), ("WARN", "speed_error_warn_range"))
        self.assertEqual(validator.edge_speed_row_status(3.0), ("PASS", "within_pass_range"))
        self.assertEqual(validator.edge_speed_row_status(None), ("WARN", "no_observed_speed"))

    def test_lane_match_status_uses_mode_median_and_max_lane_count(self) -> None:
        self.assertEqual(validator.lane_match_status(3.0, [3, 3, 2])[0], "PASS")
        self.assertEqual(validator.lane_match_status(2.0, [2, 3])[0], "PASS")
        self.assertEqual(validator.lane_match_status(3.0, [2, 2, 3])[0], "WARN")
        self.assertEqual(validator.lane_match_status(3.0, [2, 2, 2])[0], "FAIL")

    def test_net_projection_parses_active_utm_zone(self) -> None:
        projection = validator.parse_net_projection(PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml")

        assert projection is not None
        self.assertEqual(projection.utm_zone, 52)
        self.assertTrue(projection.northern)


class B0RealityValidationInputTest(unittest.TestCase):
    def test_select_b0_row_ignores_failed_and_non_b0_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            edge_data = tmp / "edgeData.xml"
            edge_data.write_text("<meandata><interval begin=\"0\" end=\"1\" /></meandata>", encoding="utf-8")
            results_csv = tmp / "experiment_results.csv"
            with results_csv.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=["mode", "parameter_id", "final_status", "edgeData_output", "run_id"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "mode": "B2",
                        "parameter_id": "selected",
                        "final_status": "PASS",
                        "edgeData_output": str(edge_data),
                        "run_id": "wrong",
                    }
                )
                writer.writerow(
                    {
                        "mode": "B0",
                        "parameter_id": "no_control",
                        "final_status": "FAIL",
                        "edgeData_output": str(edge_data),
                        "run_id": "failed",
                    }
                )
                writer.writerow(
                    {
                        "mode": "B0",
                        "parameter_id": "no_control",
                        "final_status": "PASS_WITH_REMAINING_BACKGROUND",
                        "edgeData_output": str(edge_data),
                        "run_id": "selected",
                    }
                )

            row = validator.select_b0_row_from_results(results_csv)

        assert row is not None
        self.assertEqual(row["run_id"], "selected")

    def test_parse_edge_data_accumulates_counts_and_weighted_speed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            edge_data = Path(tmp_dir) / "edgeData.xml"
            edge_data.write_text(
                """<meandata>
                  <interval begin="0" end="10">
                    <edge id="edge-a" sampledSeconds="2" speed="5" entered="1" left="3" departed="0" arrived="0" />
                  </interval>
                  <interval begin="10" end="20">
                    <edge id="edge-a" sampledSeconds="4" speed="8" entered="2" left="1" departed="0" arrived="0" />
                  </interval>
                </meandata>""",
                encoding="utf-8",
            )

            begin, end, values = validator.parse_edge_data(edge_data)

        self.assertEqual(begin, 0.0)
        self.assertEqual(end, 20.0)
        self.assertAlmostEqual(values["edge-a"]["speed_mps"], (2 * 5 + 4 * 8) / 6)
        self.assertEqual(values["edge-a"]["screenline_count"], 4.0)

    def test_representative_demand_edge_prefers_observed_screenline_count(self) -> None:
        class FakeNet:
            def convertLonLat2XY(self, lon: float, lat: float) -> tuple[float, float]:
                return lon, lat

        segment = validator.Segment(
            segment_id="S1",
            start_intersection="a",
            end_intersection="b",
            length_m=10.0,
            upbound_lanes=1.0,
            downbound_lanes=1.0,
            speed_limit_kmh=50.0,
            avg_speed_kmh_upbound=10.0,
            avg_speed_kmh_downbound=10.0,
            travel_time_s_upbound=1.0,
            travel_time_s_downbound=1.0,
            reference_vph=100.0,
            start_lat=0.0,
            start_lon=0.0,
            end_lat=0.0,
            end_lon=10.0,
        )
        near_zero = validator.EdgeFeature("near-zero", [(4.5, 0.0), (5.5, 0.0)], 1.0, 1, 10.0, 0.0)
        far_active = validator.EdgeFeature("far-active", [(1.0, 0.0), (2.0, 0.0)], 1.0, 1, 10.0, 0.0)
        edge_data = {
            "near-zero": {"screenline_count": 0.0},
            "far-active": {"screenline_count": 7.0},
        }

        representative = validator.representative_demand_edge_id(
            FakeNet(),
            segment,
            "upbound",
            [near_zero, far_active],
            edge_data,
        )

        self.assertEqual(representative, "far-active")


class B0RealityValidationRecommendationTest(unittest.TestCase):
    def test_recommendations_include_global_and_segment_actions(self) -> None:
        demand_rows = [
            {
                "segment_id": "S1",
                "direction": "upbound",
                "scaled_reference_count": 10.0,
                "observed_count": 5.0,
                "scaled_recall": 0.5,
            },
            {
                "segment_id": "S1",
                "direction": "downbound",
                "scaled_reference_count": 10.0,
                "observed_count": 20.0,
                "scaled_recall": 2.0,
            },
            {
                "segment_id": "S2",
                "direction": "upbound",
                "scaled_reference_count": 10.0,
                "observed_count": 0.0,
                "scaled_recall": 0.0,
            },
        ]

        rows, summary = validator.build_recommendation_rows(
            demand_rows,
            demand_status="FAIL",
            overall_status="FAIL",
            warmup_scale=0.15,
            sustain_scale=0.05,
        )

        self.assertTrue(summary["recommendation_triggered"])
        self.assertAlmostEqual(summary["recommended_global_multiplier"], 1.25)
        self.assertEqual(rows[1]["action"], "increase_demand")
        self.assertEqual(rows[2]["action"], "decrease_demand")
        self.assertEqual(rows[3]["action"], "missing_flow_or_mapping")


if __name__ == "__main__":
    unittest.main()
