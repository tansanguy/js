#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import math
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py"


spec = importlib.util.spec_from_file_location("run_b0_b1_b2_experiment", RUNNER_PATH)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class DelayWindowTest(unittest.TestCase):
    def test_edge_delay_is_clipped_to_emergency_depart_window(self) -> None:
        record = runner.windowed_edge_delay_record(
            edge_id="edge-a",
            entered_at=500.0,
            left_at=650.0,
            free_flow_sec=90.0,
            window_start=600.0,
        )

        assert record is not None
        self.assertEqual(record["edge_id"], "edge-a")
        self.assertAlmostEqual(record["actual_sec"], 50.0)
        self.assertAlmostEqual(record["free_flow_sec"], 30.0)

    def test_edge_delay_before_emergency_depart_is_excluded(self) -> None:
        record = runner.windowed_edge_delay_record(
            edge_id="edge-a",
            entered_at=100.0,
            left_at=590.0,
            free_flow_sec=90.0,
            window_start=600.0,
        )

        self.assertIsNone(record)

    def test_n_delay_uses_windowed_records(self) -> None:
        records = [
            runner.windowed_edge_delay_record("edge-a", 500.0, 650.0, 90.0, 600.0),
            runner.windowed_edge_delay_record("edge-b", 620.0, 680.0, 40.0, 600.0),
        ]
        summary = runner.summarize_general_non_main_delay([record for record in records if record is not None])

        expected_delay = ((50.0 - 30.0) + (60.0 - 40.0)) / 2.0
        self.assertTrue(math.isclose(summary["N_delay_sec"], expected_delay))
        self.assertEqual(summary["general_non_main_vehicle_edge_count"], 2)


class OutputPathTest(unittest.TestCase):
    def test_nonlegacy_outputs_are_scoped_by_run_id(self) -> None:
        paths = runner.output_paths("sample_prefix", legacy=False, run_id="run_001")

        self.assertEqual(
            paths["results_csv"],
            PROJECT_ROOT / "results/metrics/sample_prefix/run_001/experiment_results.csv",
        )
        self.assertEqual(
            paths["score_components_csv"],
            PROJECT_ROOT / "results/metrics/sample_prefix/run_001/score_components.csv",
        )
        self.assertEqual(
            paths["result_score_csv"],
            PROJECT_ROOT / "results/metrics/sample_prefix/run_001/result_score.csv",
        )
        self.assertEqual(
            paths["summary_json"],
            PROJECT_ROOT / "results/metrics/sample_prefix/run_001/experiment_summary.json",
        )
        self.assertEqual(paths["latest_json"], PROJECT_ROOT / "results/metrics/sample_prefix/latest.json")

    def test_final_result_fields_hide_tripinfo_length_and_count_sequence_extensions(self) -> None:
        self.assertIn("green_arrived_before_t_change_extension_count", runner.EXPERIMENT_RESULT_FIELDS)
        self.assertNotIn("emergency_tripinfo_route_length_m", runner.EXPERIMENT_RESULT_FIELDS)


class FixedSeoulStationRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sumo_net = runner.S07.read_sumo_net(runner.DEFAULT_NET)
        cls.route = runner.synthetic_seoul_station_route(runner.DEFAULT_NET)

    def test_fixed_route_policy_and_target(self) -> None:
        self.assertEqual(self.route["route_id"], runner.SEOUL_STATION_ROUTE_ID)
        self.assertEqual(self.route["selected_policy"], "straight_seoul_station_fixed")
        self.assertEqual(self.route["target_edge_id"], "619147738#0")

    def test_fixed_route_length_is_official_external_edge_sum(self) -> None:
        edge_ids = self.route["route_edges"].split()
        external_length = runner.route_length_meters(runner.DEFAULT_NET, edge_ids)

        self.assertEqual(len(edge_ids), 59)
        self.assertAlmostEqual(float(self.route["route_length_m"]), 2990.17, places=2)
        self.assertAlmostEqual(external_length, float(self.route["route_length_m"]), places=2)

    def test_fixed_route_contains_manual_terminal_sequence(self) -> None:
        edge_ids = self.route["route_edges"].split()
        terminal_sequence = [
            "781985787#0",
            "218915135#3",
            "218915135#4",
            "781983104#0",
            "781983104#1",
            "333557072#3",
            "333557072#5",
            "619147738#0",
        ]
        start = edge_ids.index(terminal_sequence[0])

        self.assertEqual(edge_ids[start:], terminal_sequence)

    def test_fixed_route_edges_are_connected_and_unique(self) -> None:
        edge_ids = self.route["route_edges"].split()

        self.assertEqual(edge_ids[0], runner.SEOUL_STATION_START_EDGE)
        self.assertEqual(edge_ids[-1], runner.SEOUL_STATION_TARGET_EDGE)
        self.assertEqual(len(edge_ids), len(set(edge_ids)))
        for from_edge_id, to_edge_id in zip(edge_ids, edge_ids[1:], strict=False):
            from_edge = self.sumo_net.getEdge(from_edge_id)
            to_edge = self.sumo_net.getEdge(to_edge_id)
            self.assertIn(to_edge, from_edge.getOutgoing(), f"{from_edge_id}->{to_edge_id}")


class BayesianOptimizationInputTest(unittest.TestCase):
    def write_rows(self, rows: list[dict[str, str]]) -> Path:
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        temp = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", suffix=".csv", delete=False)
        with temp:
            writer = runner.csv.DictWriter(temp, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return Path(temp.name)

    def test_bo_loads_score_alias_and_excludes_failed_rows(self) -> None:
        path = self.write_rows(
            [
                {
                    "mode": "B2",
                    "parameter_id": "ok",
                    "D_det": "500",
                    "alpha": "5",
                    "G_ext": "60",
                    "A_delay_sec": "100",
                    "N_delay_sec": "4",
                    "T_recovery_sec": "10",
                    "Score": "314",
                    "final_status": "PASS",
                    "emergency_arrived": "True",
                    "emergency_teleport": "False",
                    "route_error_count": "0",
                    "sumo_exit_code": "0",
                },
                {
                    "mode": "B2",
                    "parameter_id": "bad",
                    "D_det": "700",
                    "alpha": "5",
                    "G_ext": "60",
                    "A_delay_sec": "100",
                    "N_delay_sec": "4",
                    "T_recovery_sec": "10",
                    "Score": "314",
                    "final_status": "FAIL",
                    "emergency_arrived": "False",
                    "emergency_teleport": "False",
                    "route_error_count": "0",
                    "sumo_exit_code": "0",
                },
            ]
        )

        observations, excluded, input_count, b2_count = runner.load_bo_observations([path])

        self.assertEqual(input_count, 2)
        self.assertEqual(b2_count, 2)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["score_sec"], 314.0)
        self.assertEqual(observations[0]["T_change_sec"], 10)
        self.assertEqual(len(excluded), 1)
        self.assertIn("final_status_fail", excluded[0]["exclude_reason"])
        self.assertIn("emergency_not_arrived", excluded[0]["exclude_reason"])

    def test_bo_missing_required_columns_fails_clearly(self) -> None:
        path = self.write_rows([{"mode": "B2", "D_det": "500", "Score": "10"}])

        with self.assertRaisesRegex(runner.ExperimentError, "missing_bo_required_columns"):
            runner.load_bo_observations([path])

    def test_bo_recommendations_exclude_existing_theta_and_fix_t_change(self) -> None:
        observations = [
            {"parameter_id": "p1", "D_det": 500.0, "alpha": 5.0, "G_ext": 30.0, "score_sec": 1000.0},
            {"parameter_id": "p2", "D_det": 500.0, "alpha": 6.0, "G_ext": 60.0, "score_sec": 900.0},
            {"parameter_id": "p3", "D_det": 700.0, "alpha": 5.0, "G_ext": 30.0, "score_sec": 800.0},
            {"parameter_id": "p4", "D_det": 700.0, "alpha": 6.0, "G_ext": 60.0, "score_sec": 700.0},
        ]

        recommendations, summary = runner.fit_bo_and_recommend(observations, recommend_count=10, seed=123)
        existing = {(500.0, 5.0, 30.0), (500.0, 6.0, 60.0), (700.0, 5.0, 30.0), (700.0, 6.0, 60.0)}

        self.assertEqual(len(recommendations), 10)
        for row in recommendations:
            theta = (float(row["D_det"]), float(row["alpha"]), float(row["G_ext"]))
            self.assertNotIn(theta, existing)
            self.assertEqual(row["T_change_sec"], "10")
        self.assertEqual(summary["current_best"]["parameter_id"], "p4")


if __name__ == "__main__":
    unittest.main()
