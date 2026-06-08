from __future__ import annotations

import csv
import contextlib
import io
import importlib.util
import json
import tempfile
import threading
import time
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
            "D_E_sec": "100",
            "D_G_sec": "1000",
        }

        D_E_sec, D_G_sec, score, penalty, penalized = self.runner.score_delay_row(row, 10.0, 1.0)

        self.assertEqual(D_E_sec, 100.0)
        self.assertEqual(D_G_sec, 1000.0)
        self.assertAlmostEqual(score, (10.0 / 11.0) * 100.0 + (1.0 / 11.0) * 1000.0, places=6)
        self.assertEqual(penalty, 0.0)
        self.assertEqual(penalized, score)

    def test_summary_collision_teleport_penalizes_otherwise_pass_row(self):
        row = {
            "final_status": "PASS",
            "emergency_arrived": "True",
            "emergency_teleport": "False",
            "D_E_sec": "100",
            "D_G_sec": "1000",
            "sumo_summary_teleports": "1",
            "sumo_summary_collisions": "1",
        }

        _D_E_sec, _D_G_sec, score, penalty, penalized = self.runner.score_delay_row(row, 10.0, 1.0)

        self.assertEqual(penalty, self.runner.FAILURE_PENALTY_SEC)
        self.assertAlmostEqual(penalized, score + self.runner.FAILURE_PENALTY_SEC)

    def test_failed_rows_are_included_as_penalized_bo_learning_observations(self):
        failed = {
            "final_status": "FAIL",
            "emergency_arrived": "False",
            "emergency_teleport": "False",
            "failure_reason": "emergency_stuck",
            "parameter_id": "bad",
            "t_lead": "10",
            "delta_T_thr": "20",
            "G_ext": "30",
            "Q_ratio": "0.40",
            "tau": "0.80",
            "score": str(self.runner.FAILURE_PENALTY_SEC),
            "D_G_sec": "493.75",
            "stage2_hold_count": "0",
        }
        valid = {
            **failed,
            "final_status": "PASS",
            "emergency_arrived": "True",
            "failure_reason": "",
            "parameter_id": "good",
            "score": "91.20",
        }

        failed_observation = self.runner.bo_learning_observation(failed)
        observation = self.runner.bo_learning_observation(valid)

        self.assertIsNotNone(failed_observation)
        self.assertEqual(failed_observation["bo_score_sec"], str(self.runner.FAILURE_PENALTY_SEC))
        self.assertEqual(failed_observation["bo_failed"], "True")
        self.assertEqual(failed_observation["failure_reason"], "emergency_stuck")
        self.assertIsNotNone(observation)
        self.assertEqual(observation["bo_score_sec"], "91.20")
        self.assertEqual(observation["bo_failed"], "False")

    def test_completed_round_count_uses_round_index_not_row_count(self):
        rows = [
            {"round": "1", "round_theta_index": "1"},
            {"round": "1", "round_theta_index": "2"},
            {"round": "2", "round_theta_index": "1"},
            {"round": "2", "round_theta_index": "2"},
        ]

        self.assertEqual(self.runner.completed_round_count(rows), 2)

    def test_bo_first_method_order_option(self):
        args = self.runner.parse_args(["--mock-eval", "--n", "1", "--m", "4", "--bo-initial", "2", "--bo-first"])
        self.runner.validate_args(args)

        self.assertEqual(self.runner.selected_methods(args), ["BO", "Random Search", "CMA-ES"])

    def test_default_budget_is_single_seed_six_by_fifty(self):
        args = self.runner.parse_args(["--mock-eval"])
        self.runner.validate_args(args)

        self.assertEqual(args.n, 1)
        self.assertEqual(args.m, 50)
        self.assertEqual(args.theta_per_round, 6)
        self.assertEqual(args.workers, 6)
        self.assertEqual(args.ei_candidate_count, 5000)
        self.assertEqual(args.bo_pass_focus_from_round, 0)

    def test_pass_focus_bo_batch_uses_clean_success_neighborhood(self):
        bounds = {"t_lead": {"lower": 0, "upper": 120}, "delta_T_thr": {"lower": 0, "upper": 240}, "G_ext": {"lower": 0, "upper": 50}, "Q_ratio": {"lower": 0.0, "upper": 1.0}, "tau": {"lower": 0.70, "upper": 0.90}}
        rows = [
            {
                "final_status": "PASS",
                "emergency_arrived": "True",
                "emergency_teleport": "False",
                "parameter_id": "best",
                "t_lead": "90",
                "delta_T_thr": "198",
                "G_ext": "47",
                "Q_ratio": "0.07",
                "tau": "0.79",
                "score": "277.55",
                "D_G_sec": "453.83",
            },
            {
                "final_status": "PASS",
                "emergency_arrived": "True",
                "emergency_teleport": "False",
                "parameter_id": "neighbor",
                "t_lead": "92",
                "delta_T_thr": "196",
                "G_ext": "47",
                "Q_ratio": "0.08",
                "tau": "0.80",
                "score": "277.55",
                "D_G_sec": "453.83",
            },
            {
                "final_status": "FAIL",
                "emergency_arrived": "False",
                "emergency_teleport": "False",
                "parameter_id": "fail",
                "t_lead": "20",
                "delta_T_thr": "20",
                "G_ext": "2",
                "Q_ratio": "0.99",
                "tau": "0.70",
                "score": str(self.runner.FAILURE_PENALTY_SEC),
                "D_G_sec": "500",
            },
        ]
        observations = [self.runner.bo_learning_observation(row) for row in rows]
        observations = [row for row in observations if row is not None]
        existing = {self.runner.theta_key(rows[0])}

        selected = self.runner.pass_focus_bo_batch(observations, bounds, 6, seed=123, existing=existing, min_feasibility=0.0)

        self.assertEqual(len(selected), 6)
        self.assertTrue(all(row["bo_batch_slot"] == "pass_focus" for row in selected))
        self.assertTrue(all(str(row["bo_candidate_source"]).startswith("pass_focus") for row in selected))
        self.assertNotIn(self.runner.theta_key(rows[0]), {self.runner.theta_key(row) for row in selected})
        self.assertTrue(all(abs(float(row["G_ext"]) - 47.0) <= 3.0 for row in selected[:4]))

    def test_warm_start_csv_allows_bo_from_round_one(self):
        original_essi = self.runner.essi_improvement_candidates
        calls = []

        def fake_essi(observations, bounds, stage1, seed, existing, candidate_count):
            calls.append(list(observations))
            return [{
                "t_lead": 12,
                "delta_T_thr": 34,
                "G_ext": 5,
                "Q_ratio": 0.22,
                "tau": 0.78,
                "raw_ei_acquisition": "10.00",
                "acquisition": "10.00",
                "bo_selection_strategy": "warm_start_test",
                "bo_candidate_source": "warm_start",
                "bo_plateau_mode": "False",
                "_essi_acquisition_value": 10.0,
            }]

        self.runner.essi_improvement_candidates = fake_essi
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_dir = Path(tmp) / "outputs"
                run_root = Path(tmp) / "runs"
                warm_csv = Path(tmp) / "warm.csv"
                with warm_csv.open("w", encoding="utf-8", newline="") as file:
                    writer = csv.DictWriter(file, fieldnames=self.runner.EVALUATION_FIELDS)
                    writer.writeheader()
                    writer.writerow({
                        "method": "BO",
                        "seed": "20260607",
                        "round": "7",
                        "round_theta_index": "1",
                        "parameter_id": "warm_pass",
                        "t_lead": "58",
                        "delta_T_thr": "81",
                        "G_ext": "42",
                        "Q_ratio": "0.04",
                        "tau": "0.82",
                        "score": "326.19",
                        "final_status": "PASS",
                        "emergency_arrived": "True",
                        "emergency_teleport": "False",
                    })
                    writer.writerow({
                        "method": "BO",
                        "seed": "20260607",
                        "round": "10",
                        "round_theta_index": "1",
                        "parameter_id": "warm_fail",
                        "t_lead": "78",
                        "delta_T_thr": "33",
                        "G_ext": "41",
                        "Q_ratio": "0.96",
                        "tau": "0.79",
                        "score": str(self.runner.FAILURE_PENALTY_SEC),
                        "final_status": "FAIL",
                        "failure_reason": "emergency_stuck",
                        "emergency_arrived": "False",
                        "emergency_teleport": "False",
                    })

                with contextlib.redirect_stdout(io.StringIO()):
                    code = self.runner.main([
                        "--mock-eval",
                        "--run-id",
                        "warm_start_contract",
                        "--methods",
                        "bo",
                        "--n",
                        "1",
                        "--m",
                        "2",
                        "--theta-per-round",
                        "1",
                        "--bo-initial",
                        "0",
                        "--ei-candidate-count",
                        "10",
                        "--warm-start-csv",
                        str(warm_csv),
                        "--workers",
                        "1",
                        "--output-dir",
                        str(output_dir),
                        "--run-root",
                        str(run_root),
                        "--skip-pareto",
                        "--skip-noise-check",
                    ])
                self.assertEqual(code, 0)
                self.assertGreaterEqual(len(calls[0]), 2)
                with (output_dir / "warm_start_contract" / "all_evaluations.csv").open("r", encoding="utf-8", newline="") as file:
                    rows = list(csv.DictReader(file))
                self.assertEqual(rows[0]["round"], "1")
                self.assertEqual(rows[0]["bo_selection_strategy"], "warm_start_test")
                preflight = self.runner.read_json(output_dir / "warm_start_contract" / "preflight_summary.json")
                self.assertEqual(preflight["warm_start"]["observation_count"], 2)
        finally:
            self.runner.essi_improvement_candidates = original_essi

    def test_diverse_bo_batch_skips_near_duplicate_candidates(self):
        bounds = {"t_lead": {"lower": 0, "upper": 100}, "delta_T_thr": {"lower": 0, "upper": 100}, "G_ext": {"lower": 0, "upper": 50}, "Q_ratio": {"lower": 0.0, "upper": 1.0}, "tau": {"lower": 0.70, "upper": 0.90}}
        ranked = [
            {"t_lead": 50, "delta_T_thr": 50, "G_ext": 25, "Q_ratio": 0.50, "tau": 0.80, "_essi_acquisition_value": 10.0},
            {"t_lead": 51, "delta_T_thr": 50, "G_ext": 25, "Q_ratio": 0.50, "tau": 0.80, "_essi_acquisition_value": 9.0},
            {"t_lead": 80, "delta_T_thr": 20, "G_ext": 40, "Q_ratio": 0.80, "tau": 0.88, "_essi_acquisition_value": 8.0},
        ]

        selected = self.runner.diverse_bo_batch(ranked, bounds, 2, min_distance=0.08)

        self.assertEqual(len(selected), 2)
        self.assertEqual(self.runner.theta_key(selected[0]), self.runner.theta_key(ranked[0]))
        self.assertEqual(self.runner.theta_key(selected[1]), self.runner.theta_key(ranked[2]))

    def test_diverse_bo_batch_uses_smoke_methodology_slots(self):
        bounds = {"t_lead": {"lower": 0, "upper": 100}, "delta_T_thr": {"lower": 0, "upper": 120}, "G_ext": {"lower": 0, "upper": 50}, "Q_ratio": {"lower": 0.0, "upper": 1.0}, "tau": {"lower": 0.70, "upper": 0.90}}
        ranked = [
            {"t_lead": 48, "delta_T_thr": 75, "G_ext": 42, "Q_ratio": 0.13, "tau": 0.88, "_essi_acquisition_value": 100.0, "bo_selection_strategy": "stable_success_lattice", "bo_candidate_source": "stable_success"},
            {"t_lead": 49, "delta_T_thr": 76, "G_ext": 41, "Q_ratio": 0.12, "tau": 0.88, "_essi_acquisition_value": 95.0, "bo_selection_strategy": "stable_success_lattice", "bo_candidate_source": "stable_success"},
            {"t_lead": 50, "delta_T_thr": 77, "G_ext": 42, "Q_ratio": 0.11, "tau": 0.88, "_essi_acquisition_value": 90.0, "bo_selection_strategy": "stable_success_lattice", "bo_candidate_source": "stable_success"},
            {"t_lead": 35, "delta_T_thr": 55, "G_ext": 38, "Q_ratio": 0.25, "tau": 0.80, "_essi_acquisition_value": 70.0, "bo_candidate_source": "local"},
            {"t_lead": 65, "delta_T_thr": 95, "G_ext": 43, "Q_ratio": 0.20, "tau": 0.86, "_essi_acquisition_value": 69.0, "bo_candidate_source": "trust_region"},
            {"t_lead": 10, "delta_T_thr": 110, "G_ext": 5, "Q_ratio": 0.70, "tau": 0.72, "_essi_acquisition_value": 50.0, "bo_candidate_source": "global"},
            {"t_lead": 90, "delta_T_thr": 15, "G_ext": 48, "Q_ratio": 0.90, "tau": 0.90, "_essi_acquisition_value": 20.0, "bo_candidate_source": "global"},
        ]

        selected = self.runner.diverse_bo_batch(ranked, bounds, 6, min_distance=0.04)
        slots = [row.get("bo_batch_slot") for row in selected]

        self.assertEqual(len(selected), 6)
        self.assertEqual(slots.count("stable"), 2)
        self.assertEqual(slots.count("local_constrained"), 2)
        self.assertEqual(slots.count("global_ei"), 1)
        self.assertEqual(slots.count("space_filling"), 1)

    def test_theta_per_round_batches_all_methods(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "outputs"
            run_root = Path(tmp) / "runs"
            with contextlib.redirect_stdout(io.StringIO()):
                code = self.runner.main([
                    "--mock-eval",
                    "--run-id",
                    "batch_contract",
                    "--n",
                    "1",
                    "--m",
                    "3",
                    "--theta-per-round",
                    "2",
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
            run_dir = output_dir / "batch_contract"
            with (run_dir / "all_evaluations.csv").open("r", encoding="utf-8", newline="") as file:
                eval_rows = list(csv.DictReader(file))
            self.assertEqual(len(eval_rows), 18)
            for method in ["Random Search", "CMA-ES", "BO"]:
                method_rows = [row for row in eval_rows if row["method"] == method]
                self.assertEqual([row["round"] for row in method_rows], ["1", "1", "2", "2", "3", "3"])
                self.assertEqual([row["round_theta_index"] for row in method_rows], ["1", "2", "1", "2", "1", "2"])
                self.assertTrue(all(row["theta_per_round"] == "2" for row in method_rows))
                self.assertEqual(len({row["parameter_id"] for row in method_rows}), 6)

            with (run_dir / "table1_best_so_far.csv").open("r", encoding="utf-8", newline="") as file:
                best_rows = list(csv.DictReader(file))
            self.assertEqual(set(best_rows[0]), {"method", "seed", "R1", "R2", "R3"})

            summary = self.runner.read_json(run_dir / "experiment_summary.json")
            self.assertEqual(summary["m"], 3)
            self.assertEqual(summary["theta_per_round"], 2)
            self.assertEqual(summary["theta_evaluations_per_seed_method"], 6)

    def test_workers_parallelize_round_theta_batch(self):
        original_evaluate = self.runner.evaluate_theta
        active = 0
        max_active = 0
        lock = threading.Lock()

        def slow_evaluate(*args, **kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.10)
                return original_evaluate(*args, **kwargs)
            finally:
                with lock:
                    active -= 1

        self.runner.evaluate_theta = slow_evaluate
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_dir = Path(tmp) / "outputs"
                run_root = Path(tmp) / "runs"
                with contextlib.redirect_stdout(io.StringIO()):
                    code = self.runner.main([
                        "--mock-eval",
                        "--run-id",
                        "workers_contract",
                        "--methods",
                        "bo",
                        "--n",
                        "1",
                        "--m",
                        "3",
                        "--theta-per-round",
                        "6",
                        "--bo-initial",
                        "2",
                        "--workers",
                        "6",
                        "--output-dir",
                        str(output_dir),
                        "--run-root",
                        str(run_root),
                        "--skip-pareto",
                        "--skip-noise-check",
                    ])
            self.assertEqual(code, 0)
            self.assertGreaterEqual(max_active, 6)
        finally:
            self.runner.evaluate_theta = original_evaluate

    def test_resume_continues_from_method_seed_checkpoint(self):
        original_evaluate = self.runner.evaluate_theta
        calls = 0

        def interrupt_after_two(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 2:
                raise RuntimeError("synthetic_interrupt")
            return original_evaluate(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "outputs"
            run_root = Path(tmp) / "runs"
            common = [
                "--mock-eval",
                "--run-id",
                "resume_contract",
                "--methods",
                "bo",
                "--n",
                "1",
                "--m",
                "4",
                "--theta-per-round",
                "1",
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
            args = self.runner.parse_args(common)
            self.runner.validate_args(args)
            self.runner.evaluate_theta = interrupt_after_two
            try:
                with self.assertRaises(RuntimeError):
                    self.runner.run_experiment(args)
            finally:
                self.runner.evaluate_theta = original_evaluate

            checkpoint = output_dir / "resume_contract" / "checkpoints" / f"bo_{self.runner.DEFAULT_SEED_BASE}.csv"
            with checkpoint.open("r", encoding="utf-8", newline="") as file:
                checkpoint_rows = list(csv.DictReader(file))
            self.assertEqual(len(checkpoint_rows), 2)

            with contextlib.redirect_stdout(io.StringIO()):
                code = self.runner.main([*common, "--resume"])
            self.assertEqual(code, 0)
            with (output_dir / "resume_contract" / "all_evaluations.csv").open("r", encoding="utf-8", newline="") as file:
                eval_rows = list(csv.DictReader(file))
            self.assertEqual(len(eval_rows), 4)
            self.assertEqual([row["round"] for row in eval_rows], ["1", "2", "3", "4"])

    def test_collect_visualization_info_requires_and_writes_fcd_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "outputs"
            run_root = Path(tmp) / "runs"
            run_id = "viz_contract"
            parameter_id = "bo_r01_001_tl1_dt50_ge2_qr30_tau80"
            run_dir = output_dir / run_id
            run_dir.mkdir(parents=True)
            with (run_dir / "all_evaluations.csv").open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=self.runner.EVALUATION_FIELDS)
                writer.writeheader()
                writer.writerow({
                    "method": "BO",
                    "seed": str(self.runner.DEFAULT_SEED_BASE),
                    "round": "1",
                    "round_theta_index": "1",
                    "theta_per_round": "6",
                    "parameter_id": parameter_id,
                    "score": "100.00",
                    "final_status": "PASS",
                    "emergency_arrived": "True",
                    "emergency_teleport": "False",
                })

            b04_dir = run_root / run_id / "B04" / "no_control" / "repeat_001"
            b4_dir = run_root / f"{run_id}_bo_{self.runner.DEFAULT_SEED_BASE}" / "B4" / parameter_id / "repeat_001"
            b04_dir.mkdir(parents=True)
            b4_dir.mkdir(parents=True)
            for path in [
                b04_dir / "fcd.xml",
                b04_dir / "tripinfo.xml",
                b04_dir / "tls_states.csv",
                b4_dir / "fcd.xml",
                b4_dir / "tripinfo.xml",
                b4_dir / "tls_states.csv",
                b4_dir / "signal_events.csv",
            ]:
                path.write_text("stub\n", encoding="utf-8")

            manifest = run_dir / "viz_manifest.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = self.runner.main([
                    "--run-id",
                    run_id,
                    "--collect-visualization-info",
                    "--visualization-solution",
                    parameter_id,
                    "--visualization-output",
                    str(manifest),
                    "--output-dir",
                    str(output_dir),
                    "--run-root",
                    str(run_root),
                ])
            self.assertEqual(code, 0)
            payload = self.runner.read_json(manifest)
            self.assertEqual(payload["solution"]["parameter_id"], parameter_id)
            self.assertIn("b04_fcd", payload["paths"])
            self.assertIn("b04_tls_states", payload["paths"])
            self.assertIn("b4_tls_states", payload["paths"])
            self.assertIn("b4_signal_events", payload["paths"])
            self.assertEqual(payload["best_theta"]["parameter_id"], parameter_id)
            self.assertIn("net_sha256", payload["static_inputs"])
            self.assertFalse(payload["materialized_logs"])

    def test_emit_tls_states_flows_to_eval_args(self):
        args = self.runner.parse_args([
            "--mock-eval",
            "--run-id",
            "tls_contract",
            "--n",
            "1",
            "--m",
            "4",
            "--bo-initial",
            "2",
            "--emit-fcd",
            "--emit-tls-states",
        ])
        self.runner.validate_args(args)

        eval_args = self.runner.build_eval_args(args, args.seed_base, args.run_root, args.output_dir / "tls_contract")

        self.assertTrue(eval_args.emit_fcd)
        self.assertTrue(eval_args.emit_tls_states)

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
                    "--theta-per-round",
                    "1",
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
            self.assertEqual(final_fields[final_fields.index("score") - 3:final_fields.index("score")], ["weight_E", "weight_G", "weight_ratio"])
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
                    "output_D_E_sec",
                    "output_D_G_sec",
                    "weight_E",
                    "weight_G",
                    "weight_ratio",
                    "score",
                    "measured_T_free_EMV_sec",
                    "measured_T_actual_EMV_sec",
                    "measured_D_E_sec",
                    "measured_D_G_sec",
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
                "--theta-per-round",
                "1",
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

    def test_ei_drives_bo_selection_with_essi_as_small_adjustment(self):
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

            self.assertEqual(float(essi_ranked[0]["t_lead"]), 1.0)
            self.assertAlmostEqual(float(essi_ranked[0]["essi_acquisition"]), 0.0)
            self.assertAlmostEqual(float(essi_ranked[1]["essi_acquisition"]), 8.0)
        finally:
            self.runner.theta_bo.expected_improvement_candidates = original_ei
            self.runner.bo_spatial_subspaces = original_subspaces
            self.runner.essi_activation_values = original_activation

    def test_bo_records_raw_ei_before_feasibility_penalty(self):
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
            {"t_lead": 1, "delta_T_thr": 50, "G_ext": 1, "Q_ratio": 0.1, "tau": 0.72, "raw_acquisition": 20.0, "acquisition": 5.0},
            {"t_lead": 9, "delta_T_thr": 50, "G_ext": 9, "Q_ratio": 0.9, "tau": 0.88, "raw_acquisition": 8.0, "acquisition": 8.0},
        ]
        try:
            self.runner.theta_bo.expected_improvement_candidates = lambda *args, **kwargs: candidates
            self.runner.bo_spatial_subspaces = lambda _stage1: [{"subspace": index + 1, "weight": 1.0} for index in range(6)]
            self.runner.essi_activation_values = lambda theta, _bounds, _subspaces: [0.0 for _ in range(6)]

            ranked = self.runner.essi_improvement_candidates([], bounds, object(), 1, set(), 10)

            self.assertEqual(float(ranked[0]["t_lead"]), 9.0)
            self.assertAlmostEqual(float(ranked[1]["raw_ei_acquisition"]), 20.0)
            self.assertLess(float(ranked[1]["acquisition"]), float(ranked[1]["raw_ei_acquisition"]))
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
                    "--theta-per-round",
                    "1",
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
            self.assertEqual(final_sensitivity_fields[final_sensitivity_fields.index("score") - 3:final_sensitivity_fields.index("score")], ["weight_E", "weight_G", "weight_ratio"])
            self.assertEqual([row["weight_ratio"] for row in final_sensitivity_rows], ["1:1", "5:1", "10:1", "15:1", "20:1"])
            with (run_dir / "experiment_summary.json").open("r", encoding="utf-8") as file:
                summary = json.load(file)
            self.assertEqual(summary["pareto_protocol"]["search_runs_per_weight"], 1)
            self.assertEqual(summary["pareto_protocol"]["weight_ratios"], ["1:1", "5:1", "10:1", "15:1", "20:1"])

            with (run_dir / "noise_check_5repeat.csv").open("r", encoding="utf-8", newline="") as file:
                noise_rows = list(csv.DictReader(file))
            self.assertEqual(len(noise_rows), 5)
            self.assertGreater(len({row["score"] for row in noise_rows}), 1)


if __name__ == "__main__":
    unittest.main()
