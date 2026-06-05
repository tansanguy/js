from __future__ import annotations

import csv
import contextlib
import io
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = PROJECT_ROOT / "09 Compact Corridor Baseline/b4_runtime.py"
BO_RUNNER_PATH = PROJECT_ROOT / "09 Compact Corridor Baseline/run_b4_theta_bo.py"


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class B4ThetaRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = load_script("b4_theta_runtime_module", RUNTIME_PATH)
        cls.stage1 = cls.runtime.B4Stage1Inputs.load()

    def test_theta_bounds_include_five_variables(self):
        bounds = self.runtime.theta_bounds_from_stage1(self.stage1)

        self.assertEqual(set(bounds), {"schema", "source", "t_lead", "ext_max", "hold_max", "tau", "d_up"})
        self.assertEqual(bounds["tau"]["values"], [0.65, 0.70, 0.75, 0.80, 0.85])
        self.assertEqual(bounds["d_up"]["values"], [1, 2, 3])
        self.assertGreaterEqual(bounds["ext_max"]["upper"], bounds["ext_max"]["lower"])

    def test_tau_is_local_fill_primary_threshold(self):
        params = self.runtime.B4ThetaParams(tau=0.75)
        thresholds = self.runtime.theta_runtime_thresholds(self.stage1.thresholds, params)

        below = self.runtime.evaluate_queue_levels(0.74, 0.0, 80.0, thresholds)
        at = self.runtime.evaluate_queue_levels(0.75, 0.0, 80.0, thresholds)

        self.assertFalse(below["control_candidate"])
        self.assertTrue(at["control_candidate"])
        self.assertEqual(at["trigger_reason"], "local_fill")

    def test_d_up_limits_bottleneck_candidate_count(self):
        movements = [movement for movement in self.stage1.movements if movement.controllable][:3]
        metrics = [
            self.runtime.MovementRuntimeMetrics(
                movement=movement,
                queue_m_proxy=80.0,
                corridor_queue_m_proxy=130.0,
                local_fill_80m=1.0,
                local_fill_100m=0.8,
                local_fill_120m=0.667,
                stopline_local_fill_100m=0.8,
                corridor_fill_250m=0.52,
                approach_speed_kmh=10.0,
                speed_observed=True,
                density=20.0,
                occupancy=10.0,
                waiting=5.0,
                time_loss=5.0,
                low_speed_count=1,
                halting_count=0,
                fast_dense_flow=False,
                signal_only_delay=False,
                control_candidate=True,
                trigger_reason="local_fill",
                traffic_pressure=True,
                operational_queue=True,
                bottleneck_risk=True,
                control_mode="bottleneck_downstream_first",
            )
            for movement in movements
        ]

        selected = self.runtime.order_stage3_candidates(metrics, movements[0].route_order_index, 3, d_up=1)

        self.assertEqual(len(selected), 1)


class B4ThetaBoRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bo = load_script("b4_theta_bo_module", BO_RUNNER_PATH)

    def test_objective_score_uses_10_to_1_delay_weights(self):
        row = {"d_EMV_sec": "10", "d_veh_sec": "2.5", "final_status": "PASS", "emergency_arrived": "True", "emergency_teleport": "False", "failed": "False"}

        score, penalty, bo_score = self.bo.score_for_row(row)

        self.assertAlmostEqual(score, 102.5)
        self.assertEqual(penalty, 0.0)
        self.assertAlmostEqual(bo_score, 102.5)

    def test_mock_bo_loop_writes_full_and_score_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            metrics_root = Path(tmp) / "metrics"
            run_root = Path(tmp) / "runs"
            with contextlib.redirect_stdout(io.StringIO()):
                result_code = self.bo.main([
                    "--mock-eval",
                    "--run-id",
                    "contract_mock",
                    "--metrics-root",
                    str(metrics_root),
                    "--run-root",
                    str(run_root),
                    "--initial-count",
                    "3",
                    "--bo-rounds",
                    "2",
                    "--bo-batch-size",
                    "2",
                    "--workers",
                    "1",
                ])

            self.assertEqual(result_code, 0)
            run_dir = metrics_root / "contract_mock"
            all_values = run_dir / "bo_all_values.csv"
            score_summary = run_dir / "bo_score_summary.csv"
            state = self.bo.read_json(run_dir / "state.json")

            self.assertTrue(all_values.is_file())
            self.assertTrue(score_summary.is_file())
            self.assertEqual(state["status"], "COMPLETE")
            self.assertEqual(state["completed_round"], 2)

            with score_summary.open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 7)
            for field in ["t_lead", "tau", "ext_max", "hold_max", "d_up", "score_sec", "bo_score_sec"]:
                self.assertIn(field, rows[0])

            with all_values.open("r", encoding="utf-8", newline="") as file:
                full_rows = list(csv.DictReader(file))
            for field in ["queue_local_fill_80m_max", "queue_local_fill_100m_max", "queue_local_fill_120m_max", "queue_corridor_fill_250m_max"]:
                self.assertIn(field, full_rows[0])

    def test_output_prefix_and_resume_latest_reuse_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            metrics_parent = Path(tmp) / "metrics"
            runs_parent = Path(tmp) / "runs"
            output_prefix = "theta_contract"
            first_args = self.bo.parse_args([
                "--mock-eval",
                "--run-id",
                "resume_contract",
                "--output-prefix",
                output_prefix,
                "--metrics-root",
                str(metrics_parent / output_prefix),
                "--run-root",
                str(runs_parent / output_prefix),
                "--initial-count",
                "3",
                "--bo-rounds",
                "0",
                "--workers",
                "1",
            ])
            self.bo.validate_args(first_args)
            with contextlib.redirect_stdout(io.StringIO()):
                first = self.bo.run_bo(first_args)

            second_args = self.bo.parse_args([
                "--mock-eval",
                "--resume",
                "--output-prefix",
                output_prefix,
                "--metrics-root",
                str(metrics_parent / output_prefix),
                "--run-root",
                str(runs_parent / output_prefix),
                "--initial-count",
                "3",
                "--bo-rounds",
                "1",
                "--workers",
                "1",
            ])
            self.bo.validate_args(second_args)
            with contextlib.redirect_stdout(io.StringIO()):
                second = self.bo.run_bo(second_args)

            self.assertEqual(first["run_id"], "resume_contract")
            self.assertEqual(second["run_id"], "resume_contract")
            self.assertEqual(second["output_prefix"], output_prefix)
            self.assertEqual(second["workers"], 1)
            latest = self.bo.read_json(metrics_parent / output_prefix / "latest.json")
            self.assertEqual(latest["run_id"], "resume_contract")


if __name__ == "__main__":
    unittest.main()
