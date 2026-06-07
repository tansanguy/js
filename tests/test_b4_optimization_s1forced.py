from __future__ import annotations

import csv
import contextlib
import io
import importlib.util
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("b4_optimization_s1forced", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class B4OptimizationS1ForcedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def test_preflight_uses_current_s1forced_five_variable_schema(self):
        args = self.runner.parse_args(["--mock-eval", "--n", "1", "--m", "4", "--bo-initial", "2"])
        self.runner.validate_args(args)

        payload = self.runner.preflight(args)

        self.assertEqual(payload["bounds"]["decision_variables"], ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"])
        self.assertEqual(args.background_route.name, "background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml")
        self.assertEqual(args.stage1_dir.name, "b4_stage1_s1forced")

    def test_score_weights_are_normalized_ratios(self):
        row = {
            "final_status": "PASS",
            "emergency_arrived": "True",
            "emergency_teleport": "False",
            "d_EMV_sec": "100",
            "d_veh_sec": "1000",
        }

        delay_a, delay_n, score, penalty, penalized = self.runner.score_delay_row(row, 10.0, 1.0)

        self.assertEqual(delay_a, 100.0)
        self.assertEqual(delay_n, 1000.0)
        self.assertAlmostEqual(score, (10.0 / 11.0) * 100.0 + (1.0 / 11.0) * 1000.0, places=6)
        self.assertEqual(penalty, 0.0)
        self.assertEqual(penalized, score)

    def test_bo_first_method_order_option(self):
        args = self.runner.parse_args(["--mock-eval", "--n", "1", "--m", "4", "--bo-initial", "2", "--bo-first"])
        self.runner.validate_args(args)

        self.assertEqual(self.runner.selected_methods(args), ["BO", "Random Search", "CMA-ES"])

    def test_mock_run_writes_best_so_far_and_surrogate_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "outputs"
            run_root = Path(tmp) / "runs"
            with contextlib.redirect_stdout(io.StringIO()):
                code = self.runner.main([
                    "--mock-eval",
                    "--run-id",
                    "contract",
                    "--n",
                    "2",
                    "--m",
                    "5",
                    "--bo-initial",
                    "2",
                    "--ei-candidate-count",
                    "20",
                    "--output-dir",
                    str(output_dir),
                    "--run-root",
                    str(run_root),
                    "--skip-pareto",
                    "--skip-noise-check",
                ])

            self.assertEqual(code, 0)
            run_dir = output_dir / "contract"
            with (run_dir / "table1_best_so_far.csv").open("r", encoding="utf-8", newline="") as file:
                best_rows = list(csv.DictReader(file))
            self.assertEqual(len(best_rows), 6)
            self.assertEqual(set(best_rows[0]), {"method", "seed", "R1", "R2", "R3", "R4", "R5"})
            for row in best_rows:
                values = [float(row[f"R{index}"]) for index in range(1, 6)]
                self.assertEqual(values, sorted(values, reverse=True))

            with (run_dir / "table2_bo_surrogate.csv").open("r", encoding="utf-8", newline="") as file:
                bo_rows = list(csv.DictReader(file))
            self.assertEqual(len(bo_rows), 10)
            for field in [
                "observed_score",
                "best_so_far",
                "surrogate_mean",
                "surrogate_ci_low",
                "surrogate_ci_high",
                "acquisition",
                "essi_acquisition",
                "essi_1",
                "essi_6",
                "essi_max",
                "essi_mean",
                "essi_log_max",
                "dominant_essi_subspace",
                "spatial_activation_score",
            ]:
                self.assertIn(field, bo_rows[0])
            self.assertNotIn("essi_spc_status", bo_rows[0])
            with (run_dir / "all_evaluations.csv").open("r", encoding="utf-8", newline="") as file:
                eval_rows = list(csv.DictReader(file))
            for field in [
                "failure_reason",
                "termination_reason",
                "emergency_arrived",
                "emergency_teleport",
                "background_teleported",
                "signal_event_count",
                "stage2_hold_count",
                "stage3_preemption_count",
                "essi_acquisition",
            ]:
                self.assertIn(field, eval_rows[0])
            self.assertNotIn("essi_spc_status", eval_rows[0])
            with (run_dir / "final_method_comparison_results.csv").open("r", encoding="utf-8", newline="") as file:
                final_reader = csv.DictReader(file)
                final_fields = final_reader.fieldnames or []
                final_rows = list(final_reader)
            self.assertEqual(len(final_rows), 30)
            self.assertEqual(final_fields[final_fields.index("score") - 3:final_fields.index("score")], ["weight_A", "weight_N", "weight_ratio"])
            self.assertEqual(
                final_fields,
                [
                    "input_method",
                    "input_seed",
                    "input_round",
                    "input_parameter_id",
                    "input_t_lead",
                    "input_delta_T_thr",
                    "input_G_ext",
                    "input_Q_ratio",
                    "input_tau",
                    "output_delay_A_sec",
                    "output_delay_N_sec",
                    "weight_A",
                    "weight_N",
                    "weight_ratio",
                    "score",
                    "measured_T_free_EMV_sec",
                    "measured_T_actual_EMV_sec",
                    "measured_d_EMV_sec",
                    "measured_d_veh_sec",
                    "measured_general_mean_travel_time_sec",
                    "stage2_on_count",
                    "stage3_on_count",
                ],
            )
            subspaces = self.runner.read_json(run_dir / "bo_spatial_subspaces.json")
            self.assertEqual(subspaces["subspace_count"], 6)
            self.assertTrue(all(0.0 <= float(row["weight"]) <= 1.0 for row in subspaces["subspaces"]))
            self.assertTrue((run_dir / "figure1_best_so_far.png").is_file())
            self.assertTrue((run_dir / "figure2_bo_surrogate.png").is_file())

    def test_can_run_bo_first_then_append_other_methods(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "outputs"
            run_root = Path(tmp) / "runs"
            common = [
                "--mock-eval",
                "--run-id",
                "staged",
                "--n",
                "1",
                "--m",
                "4",
                "--bo-initial",
                "2",
                "--ei-candidate-count",
                "20",
                "--output-dir",
                str(output_dir),
                "--run-root",
                str(run_root),
                "--skip-pareto",
                "--skip-noise-check",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                bo_code = self.runner.main([*common, "--methods", "bo"])
            self.assertEqual(bo_code, 0)

            run_dir = output_dir / "staged"
            with (run_dir / "all_evaluations.csv").open("r", encoding="utf-8", newline="") as file:
                bo_rows = list(csv.DictReader(file))
            self.assertEqual({row["method"] for row in bo_rows}, {"BO"})
            self.assertEqual(len(bo_rows), 4)

            with contextlib.redirect_stdout(io.StringIO()):
                rest_code = self.runner.main([*common, "--methods", "random", "cma", "--append-existing"])
            self.assertEqual(rest_code, 0)
            with (run_dir / "all_evaluations.csv").open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual({row["method"] for row in rows}, {"BO", "Random Search", "CMA-ES"})
            self.assertEqual(len(rows), 12)
            with (run_dir / "final_method_comparison_results.csv").open("r", encoding="utf-8", newline="") as file:
                final_rows = list(csv.DictReader(file))
            self.assertEqual(len(final_rows), 12)

    def test_essi_acquisition_drives_bo_selection(self):
        original_ei = self.runner.theta_bo.expected_improvement_candidates
        original_subspaces = self.runner.bo_spatial_subspaces
        original_activation = self.runner.essi_activation_values
        bounds = {
            "t_lead": {"lower": 0, "upper": 10},
            "delta_T_thr": {"lower": 0, "upper": 100},
            "G_ext": {"lower": 0, "upper": 10},
            "Q_ratio": {"lower": 0.0, "upper": 1.0},
            "tau": {"lower": 0.70, "upper": 0.90},
        }
        candidates = [
            {"t_lead": 1, "delta_T_thr": 50, "G_ext": 1, "Q_ratio": 0.1, "tau": 0.72, "acquisition": 10.0},
            {"t_lead": 9, "delta_T_thr": 50, "G_ext": 9, "Q_ratio": 0.9, "tau": 0.88, "acquisition": 8.0},
        ]
        try:
            self.runner.theta_bo.expected_improvement_candidates = lambda *args, **kwargs: candidates
            self.runner.bo_spatial_subspaces = lambda _stage1: [{"subspace": index + 1, "weight": 1.0} for index in range(6)]
            self.runner.essi_activation_values = lambda theta, _bounds, _subspaces: [1.0 if theta["t_lead"] == 9 else 0.0 for _ in range(6)]

            essi_ranked = self.runner.essi_improvement_candidates([], bounds, object(), 1, set(), 10)

            self.assertEqual(float(essi_ranked[0]["t_lead"]), 9.0)
            self.assertAlmostEqual(float(essi_ranked[0]["essi_acquisition"]), 8.0)
            self.assertGreater(float(essi_ranked[0]["essi_acquisition"]), float(essi_ranked[1]["essi_acquisition"]))
        finally:
            self.runner.theta_bo.expected_improvement_candidates = original_ei
            self.runner.bo_spatial_subspaces = original_subspaces
            self.runner.essi_activation_values = original_activation

    def test_bo_gp_essi_failure_is_not_random_fallback(self):
        original_ei = self.runner.theta_bo.expected_improvement_candidates
        self.runner.theta_bo.expected_improvement_candidates = lambda *args, **kwargs: []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_dir = Path(tmp) / "outputs"
                run_root = Path(tmp) / "runs"
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = self.runner.main([
                        "--mock-eval",
                        "--run-id",
                        "gp_fail",
                        "--n",
                        "1",
                        "--m",
                        "3",
                        "--bo-initial",
                        "2",
                        "--ei-candidate-count",
                        "20",
                        "--output-dir",
                        str(output_dir),
                        "--run-root",
                        str(run_root),
                        "--skip-pareto",
                        "--skip-noise-check",
                    ])
            self.assertEqual(code, 1)
        finally:
            self.runner.theta_bo.expected_improvement_candidates = original_ei

    def test_mock_run_writes_pareto_and_noise_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "outputs"
            run_root = Path(tmp) / "runs"
            with contextlib.redirect_stdout(io.StringIO()):
                code = self.runner.main([
                    "--mock-eval",
                    "--run-id",
                    "pareto_contract",
                    "--n",
                    "1",
                    "--m",
                    "4",
                    "--bo-initial",
                    "2",
                    "--ei-candidate-count",
                    "15",
                    "--output-dir",
                    str(output_dir),
                    "--run-root",
                    str(run_root),
                ])

            self.assertEqual(code, 0)
            run_dir = output_dir / "pareto_contract"
            with (run_dir / "table3_pareto.csv").open("r", encoding="utf-8", newline="") as file:
                pareto_rows = list(csv.DictReader(file))
            self.assertEqual(len(pareto_rows), 5)
            for field in ["essi_max", "essi_log_max", "essi_log_max_ewma", "spc_status", "spc_stop_recommended"]:
                self.assertIn(field, pareto_rows[0])
            self.assertEqual(sum(row["is_knee"] == "True" for row in pareto_rows), 1)
            self.assertTrue((run_dir / "figure3_pareto.png").is_file())
            self.assertTrue((run_dir / "figure4_sensitivity_spc.png").is_file())
            with (run_dir / "table4_sensitivity_spc.csv").open("r", encoding="utf-8", newline="") as file:
                spc_rows = list(csv.DictReader(file))
            self.assertTrue(spc_rows)
            with (run_dir / "final_sensitivity_results.csv").open("r", encoding="utf-8", newline="") as file:
                final_sensitivity_reader = csv.DictReader(file)
                final_sensitivity_fields = final_sensitivity_reader.fieldnames or []
                final_sensitivity_rows = list(final_sensitivity_reader)
            self.assertEqual(len(final_sensitivity_rows), 5)
            self.assertEqual(final_sensitivity_fields[final_sensitivity_fields.index("score") - 3:final_sensitivity_fields.index("score")], ["weight_A", "weight_N", "weight_ratio"])
            self.assertEqual([row["weight_ratio"] for row in final_sensitivity_rows], ["1:1", "5:1", "10:1", "15:1", "20:1"])

            with (run_dir / "noise_check_5repeat.csv").open("r", encoding="utf-8", newline="") as file:
                noise_rows = list(csv.DictReader(file))
            self.assertEqual(len(noise_rows), 5)
            self.assertGreater(len({row["score"] for row in noise_rows}), 1)


if __name__ == "__main__":
    unittest.main()
