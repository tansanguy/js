from __future__ import annotations

import csv
import contextlib
import io
import importlib.util
import sys
import tempfile
import types
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

        self.assertEqual(bounds["decision_variables"], ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"])
        for field in bounds["decision_variables"]:
            self.assertIn(field, bounds)
        self.assertEqual(bounds["Q_ratio"]["lower"], 0.0)
        self.assertEqual(bounds["Q_ratio"]["upper"], 1.0)
        self.assertEqual(bounds["tau"]["lower"], 0.70)
        self.assertEqual(bounds["tau"]["upper"], 0.90)
        self.assertEqual(bounds["G_ext"]["lower"], 0)
        self.assertEqual(bounds["source"], "B4Stage1Inputs+B04_signal_program_proxy")
        self.assertGreater(bounds["t_lead"]["upper"], 0)
        self.assertGreaterEqual(bounds["G_ext"]["upper"], bounds["G_ext"]["lower"])
        self.assertEqual(bounds["fixed_structure_params"], {"hold_max": 14.0, "d_up": 1})

    def test_theta_default_is_patched_b4_default(self):
        params = self.runtime.B4ThetaParams()

        self.assertEqual(params.parameter_id, self.runtime.B4_PARAMETER_ID)
        self.assertEqual(params.t_lead, 21.0)
        self.assertEqual(params.delta_T_thr, 80.0)
        self.assertEqual(params.G_ext, 32.0)
        self.assertEqual(params.Q_ratio, 0.0)
        self.assertEqual(params.tau, 0.75)
        self.assertEqual(params.ext_max, params.G_ext)
        self.assertEqual(params.hold_max, 14.0)
        self.assertEqual(params.d_up, 1)
        self.assertNotIn("alpha", params.as_result_fields())
        self.assertNotIn("Q_trig", params.as_result_fields())

    def test_mvp_default_uses_stage1_hold_max_contract(self):
        params = self.runtime.B4MvpParams()

        self.assertEqual(params.hold_max, 14.0)

    def test_green_only_csv_tls_keep_missing_red_phase_as_none(self):
        green_only = {
            movement.tls_id: movement
            for movement in self.stage1.movements
            if getattr(movement, "green_only_no_red_phase", False)
        }

        self.assertEqual(set(green_only), {
            "CSV_TLS_S11_S12_TOEGYE_RO_2_GA",
        })
        for movement in green_only.values():
            self.assertIsNone(movement.selected_red_phase)
            self.assertFalse(movement.red_phase_available)

    def test_tau_no_longer_overrides_local_fill_primary_threshold(self):
        params = self.runtime.B4ThetaParams(tau=0.75)
        thresholds = self.runtime.theta_runtime_thresholds(self.stage1.thresholds, params)

        below = self.runtime.evaluate_queue_levels(0.74, 0.0, 80.0, thresholds)
        at = self.runtime.evaluate_queue_levels(0.75, 0.0, 80.0, thresholds)

        self.assertTrue(below["control_candidate"])
        self.assertTrue(at["control_candidate"])
        self.assertEqual(at["trigger_reason"], "local_fill")

    def test_tau_keeps_base_low_speed_threshold(self):
        permissive = self.runtime.theta_runtime_thresholds(self.stage1.thresholds, self.runtime.B4ThetaParams(tau=0.65))
        strict = self.runtime.theta_runtime_thresholds(self.stage1.thresholds, self.runtime.B4ThetaParams(tau=0.85))

        self.assertAlmostEqual(permissive.speed_trigger_kmh, self.stage1.thresholds.speed_trigger_kmh)
        self.assertAlmostEqual(strict.speed_trigger_kmh, self.stage1.thresholds.speed_trigger_kmh)
        self.assertEqual(
            self.runtime.evaluate_queue_levels(0.0, 0.0, 14.0, permissive)["trigger_reason"],
            "low_speed",
        )
        self.assertEqual(
            self.runtime.evaluate_queue_levels(0.0, 0.0, 14.0, strict)["trigger_reason"],
            "low_speed",
        )

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

        selected_one = self.runtime.order_stage3_candidates(metrics, movements[0].route_order_index, 3, d_up=1)
        selected_two = self.runtime.order_stage3_candidates(metrics, movements[0].route_order_index, 3, d_up=2)
        selected_three = self.runtime.order_stage3_candidates(metrics, movements[0].route_order_index, 3, d_up=3)

        self.assertEqual(len(selected_one), 1)
        self.assertEqual(len(selected_two), 2)
        self.assertEqual(len(selected_three), 3)


class B4ThetaBoRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bo = load_script("b4_theta_bo_module", BO_RUNNER_PATH)

    def test_default_inputs_use_s1forced_canonical_profile(self):
        args = self.bo.parse_args(["--mock-eval"])
        self.bo.validate_args(args)

        self.assertEqual(args.net_file, self.bo.B04_NET.resolve())
        self.assertEqual(args.background_route, self.bo.B04_AA_BACKGROUND_ROUTE.resolve())
        self.assertEqual(args.stage1_dir, self.bo.STAGE1_DIR.resolve())

    def test_objective_score_uses_normalized_10_to_1_delay_weights(self):
        row = {"D_E_sec": "10", "D_G_sec": "2.5", "final_status": "PASS", "emergency_arrived": "True", "emergency_teleport": "False", "failed": "False"}

        score, penalty, bo_score = self.bo.score_for_row(row)

        self.assertAlmostEqual(score, (10.0 / 11.0) * 10.0 + (1.0 / 11.0) * 2.5, places=6)
        self.assertEqual(penalty, 0.0)
        self.assertAlmostEqual(bo_score, score)

    def test_summary_collision_teleport_penalizes_otherwise_pass_row(self):
        row = {
            "D_E_sec": "10",
            "D_G_sec": "2.5",
            "final_status": "PASS",
            "emergency_arrived": "True",
            "emergency_teleport": "False",
            "failed": "False",
            "sumo_summary_teleports": "1",
            "sumo_summary_collisions": "1",
        }

        score, penalty, bo_score = self.bo.score_for_row(row)

        self.assertEqual(penalty, self.bo.FAILURE_PENALTY_SEC)
        self.assertAlmostEqual(bo_score, score + self.bo.FAILURE_PENALTY_SEC)

    def test_objective_score_accepts_external_weights(self):
        row = {"D_E_sec": "10", "D_G_sec": "2.5", "final_status": "PASS", "emergency_arrived": "True", "emergency_teleport": "False", "failed": "False"}

        score, _penalty, bo_score = self.bo.score_for_row(row, w_E=20.0, w_G=1.0)

        self.assertAlmostEqual(score, (20.0 / 21.0) * 10.0 + (1.0 / 21.0) * 2.5, places=6)
        self.assertAlmostEqual(bo_score, score)

    def test_objective_score_prefers_delay_fields_over_actual_travel_time(self):
        row = {
            "T_actual_EMV_sec": "100",
            "D_E_sec": "10",
            "general_mean_travel_time_sec": "30",
            "D_G_sec": "2.5",
            "final_status": "PASS",
            "emergency_arrived": "True",
            "emergency_teleport": "False",
            "failed": "False",
        }

        score, _penalty, bo_score = self.bo.score_for_row(row, w_E=10.0, w_G=1.0)

        self.assertAlmostEqual(score, (10.0 / 11.0) * 10.0 + (1.0 / 11.0) * 2.5, places=6)
        self.assertAlmostEqual(bo_score, score)

    def test_g_ext_zero_is_preserved_by_theta_clamp(self):
        runtime = load_script("b4_theta_runtime_for_ext_zero", RUNTIME_PATH)
        params = runtime.B4ThetaParams(G_ext=0)
        bo = self.bo
        bounds = {"t_lead": {"lower": 0, "upper": 10}, "delta_T_thr": {"lower": 0, "upper": 100}, "G_ext": {"lower": 0, "upper": 20}, "Q_ratio": {"lower": 0.0, "upper": 1.0}, "tau": {"lower": 0.70, "upper": 0.90}}

        clamped = bo.clamp_theta({"t_lead": 1, "delta_T_thr": 10, "G_ext": 0, "Q_ratio": 0.25, "tau": 0.80}, bounds)

        self.assertEqual(params.G_ext, 0)
        self.assertEqual(clamped["G_ext"], 0)
        self.assertEqual(clamped["Q_ratio"], 0.25)
        self.assertEqual(clamped["tau"], 0.80)

    def test_sklearn_fallback_recommends_batch(self):
        bounds = {"t_lead": {"lower": 0, "upper": 10}, "delta_T_thr": {"lower": 0, "upper": 100}, "G_ext": {"lower": 0, "upper": 10}, "Q_ratio": {"lower": 0.0, "upper": 1.0}, "tau": {"lower": 0.70, "upper": 0.90}}
        observations = [
            {"mode": self.bo.B4_MODE, "parameter_id": "a", "t_lead": 0, "delta_T_thr": 20, "G_ext": 0, "Q_ratio": 0.0, "tau": 0.70, "score_sec": 300.0, "bo_score_sec": 300.0},
            {"mode": self.bo.B4_MODE, "parameter_id": "b", "t_lead": 5, "delta_T_thr": 50, "G_ext": 5, "Q_ratio": 0.4, "tau": 0.80, "score_sec": 200.0, "bo_score_sec": 200.0},
            {"mode": self.bo.B4_MODE, "parameter_id": "c", "t_lead": 10, "delta_T_thr": 80, "G_ext": 10, "Q_ratio": 1.0, "tau": 0.90, "score_sec": 250.0, "bo_score_sec": 250.0},
        ]
        existing = {self.bo.theta_key(row) for row in observations}

        recommendations = self.bo.recommend_bo_batch_sklearn(observations, bounds, 2, 123, existing)

        self.assertEqual(len(recommendations), 2)
        for row in recommendations:
            self.assertNotIn(self.bo.theta_key(row), existing)

    def test_local_candidates_sample_near_best_observation(self):
        bounds = {"t_lead": {"lower": 0, "upper": 122}, "delta_T_thr": {"lower": 0, "upper": 244}, "G_ext": {"lower": 0, "upper": 50}, "Q_ratio": {"lower": 0.0, "upper": 1.0}, "tau": {"lower": 0.70, "upper": 0.90}}
        best = {"parameter_id": "best", "t_lead": 31, "delta_T_thr": 28, "G_ext": 30, "Q_ratio": 0.19, "tau": 0.81, "bo_score_sec": 91.2}
        worse = {"parameter_id": "worse", "t_lead": 120, "delta_T_thr": 200, "G_ext": 50, "Q_ratio": 1.0, "tau": 0.90, "bo_score_sec": 300.0}

        candidates = self.bo.local_theta_candidates(bounds, [best, worse], 20, 123, {self.bo.theta_key(best), self.bo.theta_key(worse)})

        self.assertEqual(len(candidates), 20)
        self.assertTrue(any(abs(float(row["t_lead"]) - 31.0) <= 15.0 and abs(float(row["Q_ratio"]) - 0.19) <= 0.20 for row in candidates))

    def test_plateau_detection_and_trust_region_candidates_focus_best_basin(self):
        bounds = {"t_lead": {"lower": 0, "upper": 122}, "delta_T_thr": {"lower": 0, "upper": 244}, "G_ext": {"lower": 0, "upper": 50}, "Q_ratio": {"lower": 0.0, "upper": 1.0}, "tau": {"lower": 0.70, "upper": 0.90}}
        rows = []
        for round_index in range(1, 12):
            rows.append({
                "round": round_index,
                "parameter_id": f"r{round_index}",
                "t_lead": 55,
                "delta_T_thr": 52,
                "G_ext": 38,
                "Q_ratio": 0.75,
                "tau": 0.71,
                "bo_score_sec": 178.0 if round_index >= 3 else 220.0 - round_index,
            })

        self.assertTrue(self.bo.plateau_detected(rows))
        candidates = self.bo.trust_region_theta_candidates(bounds, rows, 12, 123, {self.bo.theta_key(rows[2])})

        self.assertEqual(len(candidates), 12)
        self.assertTrue(all(abs(float(row["delta_T_thr"]) - 52.0) <= 12.0 for row in candidates))
        self.assertTrue(all(abs(float(row["tau"]) - 0.71) <= 0.021 for row in candidates))

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
            for field in ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau", "score_sec", "bo_score_sec"]:
                self.assertIn(field, rows[0])

            with all_values.open("r", encoding="utf-8", newline="") as file:
                full_rows = list(csv.DictReader(file))
            for field in ["queue_local_fill_80m_max", "queue_local_fill_100m_max", "queue_local_fill_120m_max", "queue_corridor_fill_250m_max"]:
                self.assertIn(field, full_rows[0])

            with (run_dir / "bo_rounds.csv").open("r", encoding="utf-8", newline="") as file:
                round_rows = list(csv.DictReader(file))
            self.assertEqual(len(round_rows), 3)
            for field in ["essi_1", "essi_2", "essi_3", "essi_4", "essi_5", "essi_6", "essi_max", "essi_mean", "essi_log_max_ewma", "essi_spc_status"]:
                self.assertIn(field, round_rows[0])
                self.assertNotEqual(round_rows[-1][field], "")
            summary = self.bo.read_json(run_dir / "bo_loop_summary.json")
            self.assertEqual(summary["weights"], {"w_E": 10.0, "w_G": 1.0})

    def test_spc_min_rounds_counts_bo_rounds_not_initial_round(self):
        original_ei = self.bo.expected_improvement_candidates
        original_subspaces = self.bo.default_route_subspaces
        self.bo.expected_improvement_candidates = lambda *args, **kwargs: [{"acquisition": 1.0}]
        self.bo.default_route_subspaces = lambda _stage1: [{"weight": 1.0} for _ in range(6)]
        args = types.SimpleNamespace(spc_alpha=0.3, spc_window=2, spc_min_rounds=15, spc_min_improvement_sec=1.0, ei_candidate_count=10)
        best = {"bo_score_sec": 100.0}
        observations = [{"parameter_id": "best", "t_lead": 1, "delta_T_thr": 50, "G_ext": 1, "Q_ratio": 0.0, "tau": 0.75, "bo_score_sec": 100.0}]
        bounds = {"t_lead": {"lower": 0, "upper": 10}, "delta_T_thr": {"lower": 0, "upper": 100}, "G_ext": {"lower": 0, "upper": 10}, "Q_ratio": {"lower": 0.0, "upper": 1.0}, "tau": {"lower": 0.70, "upper": 0.90}}
        try:
            initial = {"round": "0", "phase": "initial", "best_bo_score_sec": "100.0", "essi_log_max": "0.0", "essi_log_max_ewma": "0.0"}
            thirteen_bo = [
                {"round": str(index), "phase": "bo", "best_bo_score_sec": "100.0", "essi_log_max": "0.0", "essi_log_max_ewma": "0.0"}
                for index in range(1, 14)
            ]
            warmup = self.bo.essi_round_fields(observations, bounds, object(), 1, [initial, *thirteen_bo], best, args)
            self.assertEqual(warmup["essi_spc_status"], "warmup")
            fourteen_bo = [
                {"round": str(index), "phase": "bo", "best_bo_score_sec": "100.0", "essi_log_max": "0.0", "essi_log_max_ewma": "0.0"}
                for index in range(1, 15)
            ]
            stable = self.bo.essi_round_fields(observations, bounds, object(), 1, [initial, *fourteen_bo], best, args)
            self.assertEqual(stable["essi_spc_status"], "stable")
        finally:
            self.bo.expected_improvement_candidates = original_ei
            self.bo.default_route_subspaces = original_subspaces

    def test_hold_failure_nearby_candidate_reduces_acquisition(self):
        bounds = {
            "t_lead": {"lower": 0, "upper": 100},
            "delta_T_thr": {"lower": 0, "upper": 100},
            "G_ext": {"lower": 0, "upper": 50},
            "Q_ratio": {"lower": 0.0, "upper": 1.0},
            "tau": {"lower": 0.70, "upper": 0.90},
        }
        observations = [
            {"mode": self.bo.B4_MODE, "t_lead": 30, "delta_T_thr": 28, "G_ext": 30, "Q_ratio": 0.19, "tau": 0.81, "score_sec": 94.0, "bo_score_sec": 94.0, "D_G_sec": 120.0, "stage2_hold_count": 1},
            {"mode": self.bo.B4_MODE, "t_lead": 32, "delta_T_thr": 24, "G_ext": 33, "Q_ratio": 0.18, "tau": 0.80, "score_sec": 345.0, "bo_score_sec": 345.0, "D_G_sec": 1946.0, "stage2_hold_count": 0},
        ]
        aggregated = self.bo.aggregate_observations(observations)
        near_failure = {"t_lead": 32, "delta_T_thr": 24, "G_ext": 33, "Q_ratio": 0.18, "tau": 0.80}
        far_from_failure = {"t_lead": 100, "delta_T_thr": 100, "G_ext": 0, "Q_ratio": 1.0, "tau": 0.90}

        self.assertLess(self.bo.hold_feasibility_multiplier(near_failure, aggregated, bounds), 0.1)
        self.assertLess(self.bo.hold_feasibility_multiplier(far_from_failure, aggregated, bounds), 0.5)

    def test_failed_observation_nearby_candidate_reduces_acquisition(self):
        bounds = {
            "t_lead": {"lower": 0, "upper": 100},
            "delta_T_thr": {"lower": 0, "upper": 100},
            "G_ext": {"lower": 0, "upper": 50},
            "Q_ratio": {"lower": 0.0, "upper": 1.0},
            "tau": {"lower": 0.70, "upper": 0.90},
        }
        observations = [
            {"mode": self.bo.B4_MODE, "t_lead": 30, "delta_T_thr": 28, "G_ext": 30, "Q_ratio": 0.19, "tau": 0.81, "score_sec": 94.0, "bo_score_sec": 94.0},
            {"mode": self.bo.B4_MODE, "t_lead": 78, "delta_T_thr": 150, "G_ext": 2, "Q_ratio": 0.10, "tau": 0.72, "score_sec": 1_000_000.0, "bo_score_sec": 1_000_000.0, "bo_failed": "True"},
        ]
        aggregated = self.bo.aggregate_observations(observations)
        near_failure = {"t_lead": 78, "delta_T_thr": 150, "G_ext": 2, "Q_ratio": 0.10, "tau": 0.72}
        far_from_failure = {"t_lead": 30, "delta_T_thr": 28, "G_ext": 30, "Q_ratio": 0.19, "tau": 0.81}

        self.assertTrue(self.bo.failed_bo_observations(aggregated))
        self.assertLess(self.bo.hold_feasibility_multiplier(near_failure, aggregated, bounds), 0.05)
        self.assertGreater(self.bo.hold_feasibility_multiplier(far_from_failure, aggregated, bounds), 0.99)

    def test_axis_aligned_failure_neighborhood_penalizes_candidate(self):
        bounds = {
            "t_lead": {"lower": 0, "upper": 100},
            "delta_T_thr": {"lower": 0, "upper": 120},
            "G_ext": {"lower": 0, "upper": 50},
            "Q_ratio": {"lower": 0.0, "upper": 1.0},
            "tau": {"lower": 0.70, "upper": 0.90},
        }
        observations = [
            {"mode": self.bo.B4_MODE, "t_lead": 48, "delta_T_thr": 75, "G_ext": 42, "Q_ratio": 0.13, "tau": 0.88, "score_sec": 326.0, "bo_score_sec": 326.0},
            {"mode": self.bo.B4_MODE, "t_lead": 48, "delta_T_thr": 73, "G_ext": 42, "Q_ratio": 0.14, "tau": 0.87, "score_sec": 1_000_000.0, "bo_score_sec": 1_000_000.0, "bo_failed": "True"},
        ]
        aggregated = self.bo.aggregate_observations(observations)
        near_failure = {"t_lead": 48, "delta_T_thr": 73, "G_ext": 42, "Q_ratio": 0.13, "tau": 0.88}
        outside_failure_cell = {"t_lead": 48, "delta_T_thr": 76, "G_ext": 42, "Q_ratio": 0.13, "tau": 0.88}

        failures = self.bo.failed_bo_observations(aggregated)
        self.assertTrue(self.bo.near_axis_aligned_failure(near_failure, failures))
        self.assertFalse(self.bo.near_axis_aligned_failure(outside_failure_cell, failures))
        self.assertLess(self.bo.hold_feasibility_multiplier(near_failure, aggregated, bounds), 0.25)

    def test_stable_success_lattice_keeps_safe_plateau_direction(self):
        bounds = {
            "t_lead": {"lower": 0, "upper": 100},
            "delta_T_thr": {"lower": 0, "upper": 120},
            "G_ext": {"lower": 0, "upper": 50},
            "Q_ratio": {"lower": 0.0, "upper": 1.0},
            "tau": {"lower": 0.70, "upper": 0.90},
        }
        success = {"mode": self.bo.B4_MODE, "t_lead": 48, "delta_T_thr": 75, "G_ext": 42, "Q_ratio": 0.13, "tau": 0.88, "score_sec": 326.0, "bo_score_sec": 326.0}
        failure = {"mode": self.bo.B4_MODE, "t_lead": 48, "delta_T_thr": 73, "G_ext": 42, "Q_ratio": 0.14, "tau": 0.87, "score_sec": 1_000_000.0, "bo_score_sec": 1_000_000.0, "bo_failed": "True"}
        observations = self.bo.aggregate_observations([success, failure])
        existing = {self.bo.theta_key(row) for row in observations}

        candidates = self.bo.stable_success_lattice_candidates(bounds, observations, 20, existing)

        self.assertGreater(len(candidates), 0)
        self.assertTrue(all(abs(float(row["G_ext"]) - 42.0) <= 1.0 for row in candidates))
        self.assertTrue(all(abs(float(row["Q_ratio"]) - 0.13) <= 0.011 for row in candidates))
        self.assertTrue(all(abs(float(row["tau"]) - 0.88) <= 0.011 for row in candidates))
        self.assertTrue(any(float(row["G_ext"]) in {41.0, 43.0} for row in candidates))

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

    def test_input_override_args_are_validated_and_resolved(self):
        net_file = self.bo.B04_NET
        background_route = self.bo.B04_AA_BACKGROUND_ROUTE
        args = self.bo.parse_args([
            "--mock-eval",
            "--net-file",
            str(net_file),
            "--background-route",
            str(background_route),
            "--hard-max-sim-time",
            "4000",
            "--q-ratio",
            "0.35",
            "--tau",
            "0.80",
        ])

        self.bo.validate_args(args)

        self.assertEqual(args.net_file, net_file.resolve())
        self.assertEqual(args.background_route, background_route.resolve())
        self.assertEqual(args.hard_max_sim_time, 4000.0)
        self.assertTrue(args.allow_baseline_speed_out_of_target)
        self.assertEqual(args.q_ratio, 0.35)
        self.assertEqual(args.tau, 0.80)

    def test_target15_baseline_can_be_required_explicitly(self):
        net_file = self.bo.B04_NET
        background_route = self.bo.B04_AA_BACKGROUND_ROUTE
        args = self.bo.parse_args([
            "--mock-eval",
            "--net-file",
            str(net_file),
            "--background-route",
            str(background_route),
            "--require-target15-baseline",
        ])

        self.bo.validate_args(args)

        self.assertFalse(args.allow_baseline_speed_out_of_target)

    def test_structure_lock_json_is_loaded_but_cli_overrides_win(self):
        net_file = self.bo.B04_NET
        background_route = self.bo.B04_AA_BACKGROUND_ROUTE
        with tempfile.TemporaryDirectory() as tmp:
            lock_json = Path(tmp) / "structure_param_lock_summary.json"
            self.bo.write_json(lock_json, {
                "schema": "compact_v9_B4_structure_param_lock.v1",
                "lock_status": "LOCKED",
                "selected_structure": {
                    "hold_max": 24,
                    "d_up": 2,
                },
            })
            args = self.bo.parse_args([
                "--mock-eval",
                "--net-file",
                str(net_file),
                "--background-route",
                str(background_route),
                "--structure-lock-json",
                str(lock_json),
                "--q-ratio",
                "0.25",
                "--tau",
                "0.80",
            ])

            self.bo.validate_args(args)

            self.assertEqual(args.q_ratio, 0.25)
            self.assertEqual(args.tau, 0.80)
            self.assertEqual(args.hold_max, 24.0)
            self.assertEqual(args.d_up, 2)
            self.assertEqual(args.structure_lock_info["lock_status"], "LOCKED")

    def test_q_ratio_validation_rejects_out_of_bounds_values(self):
        net_file = self.bo.B04_NET
        background_route = self.bo.B04_AA_BACKGROUND_ROUTE
        args = self.bo.parse_args([
            "--mock-eval",
            "--net-file",
            str(net_file),
            "--background-route",
            str(background_route),
            "--q-ratio",
            "1.5",
        ])

        with self.assertRaisesRegex(self.bo.B4ThetaBoError, "q_ratio_must_be_between_0_and_1"):
            self.bo.validate_args(args)


if __name__ == "__main__":
    unittest.main()
