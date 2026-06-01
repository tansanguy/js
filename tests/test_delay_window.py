#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
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

    def test_queue_recovery_waits_for_post_peak_stable_recovery(self) -> None:
        history = []
        for time_value in range(0, 1001, 10):
            if time_value < 700:
                queue = 2
            elif time_value < 760:
                queue = 12
            elif time_value < 840:
                queue = 4
            else:
                queue = 2
            history.append((float(time_value), queue))

        detail = runner.queue_recovery_detail(history, pass_time=700.0, emergency_depart=600.0)

        self.assertEqual(detail["post_peak_queue"], 12)
        self.assertAlmostEqual(detail["recovery_threshold_queue"], 3.0)
        self.assertAlmostEqual(float(detail["recovered_time_sec"]), 840.0)
        self.assertAlmostEqual(detail["recovery_sec"], 140.0)

    def test_recovery_speed_penalty_uses_preferred_congestion_floor(self) -> None:
        penalty = runner.recovery_speed_penalty_sec(12.0, 300.0)

        self.assertAlmostEqual(penalty, 60.0)


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
        self.assertIn("realized_extension_sec", runner.EXPERIMENT_RESULT_FIELDS)
        self.assertIn("trimmed_green_sec", runner.EXPERIMENT_RESULT_FIELDS)
        self.assertIn("post_pass_trim_count", runner.EXPERIMENT_RESULT_FIELDS)
        self.assertNotIn("emergency_tripinfo_route_length_m", runner.EXPERIMENT_RESULT_FIELDS)


class FakeTrafficLight:
    def __init__(self, phase: int, remaining: float) -> None:
        self.phase = phase
        self.next_switch = remaining
        self.duration: float | None = None

    def getPhase(self, tls_id: str) -> int:
        return self.phase

    def getNextSwitch(self, tls_id: str) -> float:
        return self.next_switch

    def setPhaseDuration(self, tls_id: str, duration: float) -> None:
        self.duration = duration


class FakeTraci:
    def __init__(self, phase: int, remaining: float) -> None:
        self.trafficlight = FakeTrafficLight(phase, remaining)


class PostPassAlphaTrimTest(unittest.TestCase):
    def test_post_pass_trim_cuts_remaining_g_ext_to_alpha(self) -> None:
        traci = FakeTraci(phase=3, remaining=60.0)

        action, phase_after, reason, remaining_before, set_duration, extension_delta, trimmed_green = runner.trim_green_after_pass_to_alpha(
            traci,
            {"tls_id": "tls-a", "green_phases": [3]},
            alpha_sec=5,
            sim_time=10.0,
        )

        self.assertEqual(action, "trim_green_after_pass_to_alpha")
        self.assertEqual(phase_after, 3)
        self.assertEqual(reason, "emergency_passed_tls_trim_to_alpha")
        self.assertAlmostEqual(remaining_before, 50.0)
        self.assertAlmostEqual(set_duration, 5.0)
        self.assertAlmostEqual(extension_delta, 0.0)
        self.assertAlmostEqual(trimmed_green, 45.0)
        self.assertAlmostEqual(traci.trafficlight.duration or 0.0, 5.0)

    def test_post_pass_trim_does_not_force_green_after_phase_changed(self) -> None:
        traci = FakeTraci(phase=1, remaining=12.0)

        action, phase_after, reason, remaining_before, set_duration, extension_delta, trimmed_green = runner.trim_green_after_pass_to_alpha(
            traci,
            {"tls_id": "tls-a", "green_phases": [3]},
            alpha_sec=5,
            sim_time=10.0,
        )

        self.assertEqual(action, "post_pass_no_green_trim")
        self.assertEqual(phase_after, 1)
        self.assertEqual(reason, "current_phase_not_emergency_green_no_force")
        self.assertAlmostEqual(remaining_before, 2.0)
        self.assertEqual(set_duration, "")
        self.assertAlmostEqual(extension_delta, 0.0)
        self.assertAlmostEqual(trimmed_green, 0.0)
        self.assertIsNone(traci.trafficlight.duration)

    def test_realized_extension_uses_trimmed_green(self) -> None:
        total_extension = 60.0
        trimmed_green = 45.0

        self.assertAlmostEqual(max(total_extension - trimmed_green, 0.0), 15.0)


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

    def test_bo_initial_sampler_generates_bounded_unique_rows(self) -> None:
        rows = runner.sample_bo_initial_parameters(20, "sobol", seed=20260531)
        keys = set()

        self.assertEqual(len(rows), 20)
        for row in rows:
            d_det = float(row["D_det"])
            alpha = float(row["alpha"])
            g_ext = float(row["G_ext"])
            self.assertGreaterEqual(d_det, 300.0)
            self.assertLessEqual(d_det, 1000.0)
            self.assertEqual(d_det % 50, 0)
            self.assertGreaterEqual(alpha, 0.0)
            self.assertLessEqual(alpha, 15.0)
            self.assertEqual(alpha, round(alpha))
            self.assertGreaterEqual(g_ext, 10.0)
            self.assertLessEqual(g_ext, 60.0)
            self.assertEqual(g_ext, round(g_ext))
            self.assertEqual(row["T_change_sec"], "10")
            keys.add((d_det, alpha, g_ext))
        self.assertEqual(len(keys), 20)

    def test_bo_score_uses_signal_burden_penalty(self) -> None:
        row = {
            "score_sec": "100",
            "signal_burden_penalty_sec": "25.5",
            "failure_penalty_sec": "0",
        }

        self.assertAlmostEqual(runner.bo_score_for_row(row), 125.5)

    def test_bo_state_missing_file_starts_empty(self) -> None:
        missing = Path(tempfile.gettempdir()) / "missing_bo_state_for_unit_test.json"
        if missing.exists():
            missing.unlink()

        self.assertEqual(runner.load_bo_state(missing), {})

    def test_explicit_bo_inputs_override_auto_inputs(self) -> None:
        explicit = self.write_rows(
            [
                {
                    "D_det": "500",
                    "alpha": "5",
                    "G_ext": "30",
                    "A_delay_sec": "1",
                    "N_delay_sec": "1",
                    "T_recovery_sec": "1",
                    "score_sec": "5",
                }
            ]
        )
        args = runner.SimpleNamespace(
            bo_initial_results=[explicit],
            bo_auto_inputs=True,
            bo_results_prefix="parameter_input_sim",
        )

        self.assertEqual(runner.resolve_bo_observation_inputs(args, {"latest_results_csvs": []}), [explicit.resolve()])

    def test_skopt_dimensions_follow_expected_grid(self) -> None:
        dimensions = runner.skopt_dimensions()

        self.assertEqual(dimensions[0].categories[0], 300)
        self.assertEqual(dimensions[0].categories[-1], 1000)
        self.assertEqual(dimensions[1].categories[0], 0)
        self.assertEqual(dimensions[1].categories[-1], 15)
        self.assertEqual(dimensions[2].categories[0], 10)
        self.assertEqual(dimensions[2].categories[-1], 60)
        self.assertEqual(dimensions[2].categories[1], 11)

    def test_bo_observations_are_aggregated_by_theta_mean(self) -> None:
        observations = [
            {"parameter_id": "a", "D_det": 500.0, "alpha": 5.0, "G_ext": 30.0, "score_sec": 100.0, "bo_score_sec": 110.0},
            {"parameter_id": "a", "D_det": 500.0, "alpha": 5.0, "G_ext": 30.0, "score_sec": 120.0, "bo_score_sec": 130.0},
            {"parameter_id": "b", "D_det": 700.0, "alpha": 7.0, "G_ext": 40.0, "score_sec": 90.0, "bo_score_sec": 95.0},
        ]

        aggregated = runner.aggregate_bo_observations_by_theta(observations)
        by_theta = {(row["D_det"], row["alpha"], row["G_ext"]): row for row in aggregated}

        self.assertEqual(len(aggregated), 2)
        self.assertAlmostEqual(by_theta[(500.0, 5.0, 30.0)]["score_sec"], 110.0)
        self.assertAlmostEqual(by_theta[(500.0, 5.0, 30.0)]["bo_score_sec"], 120.0)
        self.assertEqual(by_theta[(500.0, 5.0, 30.0)]["repeat_count"], 2)

    def test_skopt_batch_recommendations_exclude_existing_theta(self) -> None:
        observations = [
            {"parameter_id": "p1", "D_det": 500.0, "alpha": 5.0, "G_ext": 30.0, "score_sec": 1000.0, "bo_score_sec": 1000.0},
            {"parameter_id": "p2", "D_det": 550.0, "alpha": 6.0, "G_ext": 35.0, "score_sec": 900.0, "bo_score_sec": 900.0},
            {"parameter_id": "p3", "D_det": 600.0, "alpha": 7.0, "G_ext": 40.0, "score_sec": 800.0, "bo_score_sec": 800.0},
        ]

        recommendations = runner.recommend_bo_batch_skopt(observations, batch_size=5, seed=123, strategy="cl_min")
        existing = {(500.0, 5.0, 30.0), (550.0, 6.0, 35.0), (600.0, 7.0, 40.0)}
        recommended = {(float(row["D_det"]), float(row["alpha"]), float(row["G_ext"])) for row in recommendations}

        self.assertEqual(len(recommendations), 5)
        self.assertEqual(len(recommended), 5)
        self.assertTrue(recommended.isdisjoint(existing))

    def test_bo_loop_mock_runs_two_rounds_and_updates_state(self) -> None:
        initial = self.write_rows(
            [
                {"mode": "B2", "parameter_id": "p1", "D_det": "500", "alpha": "5", "G_ext": "30", "A_delay_sec": "100", "N_delay_sec": "1", "T_recovery_sec": "1", "score_sec": "302", "bo_score_sec": "305", "final_status": "PASS", "emergency_arrived": "True", "emergency_teleport": "False", "route_error_count": "0", "sumo_exit_code": "0"},
                {"mode": "B2", "parameter_id": "p2", "D_det": "550", "alpha": "6", "G_ext": "35", "A_delay_sec": "90", "N_delay_sec": "1", "T_recovery_sec": "1", "score_sec": "272", "bo_score_sec": "276", "final_status": "PASS", "emergency_arrived": "True", "emergency_teleport": "False", "route_error_count": "0", "sumo_exit_code": "0"},
                {"mode": "B2", "parameter_id": "p3", "D_det": "600", "alpha": "7", "G_ext": "40", "A_delay_sec": "95", "N_delay_sec": "1", "T_recovery_sec": "1", "score_sec": "287", "bo_score_sec": "292", "final_status": "PASS", "emergency_arrived": "True", "emergency_teleport": "False", "route_error_count": "0", "sumo_exit_code": "0"},
            ]
        )
        state_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        state_file.close()
        state_path = Path(state_file.name)
        state_path.unlink()
        command = [
            sys.executable,
            str(RUNNER_PATH),
            "--bo-stage",
            "loop",
            "--bo-initial-results",
            str(initial),
            "--bo-rounds",
            "2",
            "--bo-batch-size",
            "2",
            "--bo-eval-repeats",
            "1",
            "--bo-output-prefix",
            "parameter_input_sim_bo_unit",
            "--bo-workflow-prefix",
            "parameter_input_sim_bo_unit",
            "--bo-eval-output-prefix",
            "parameter_input_sim_bo_unit_eval",
            "--bo-state",
            str(state_path),
            "--bo-mock-eval",
        ]

        completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False)

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["completed_round"], 2)
        self.assertEqual(len(state["round_result_csvs"]), 2)
        self.assertIn("top3_csv", state)
        all_results = PROJECT_ROOT / state["latest_all_results_csv"]
        all_rows = runner.read_csv(all_results)
        self.assertTrue(all_results.is_file())
        self.assertEqual({row["bo_round_index"] for row in all_rows}, {"0", "1", "2"})


if __name__ == "__main__":
    unittest.main()
