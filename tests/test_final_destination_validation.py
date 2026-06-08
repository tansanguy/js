#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import csv
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "10 Final Destination Validation/final_destination_validation.py"


def load_script():
    spec = importlib.util.spec_from_file_location("final_destination_validation_test_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FinalDestinationValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script()

    def test_departures_are_deterministic_and_in_range(self):
        first = self.module.deterministic_departures(
            seed=20260606,
            route_id="FINAL_DEST_ER_ACC_019",
            repeats=30,
            depart_min=550.0,
            depart_max=650.0,
        )
        second = self.module.deterministic_departures(
            seed=20260606,
            route_id="FINAL_DEST_ER_ACC_019",
            repeats=30,
            depart_min=550.0,
            depart_max=650.0,
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first), 30)
        self.assertTrue(all(550.0 <= value <= 650.0 for value in first))

    def test_defaults_use_s1forced_canonical_inputs(self):
        self.assertEqual(
            self.module.DEFAULT_NET,
            PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml",
        )
        self.assertEqual(
            self.module.DEFAULT_BACKGROUND_ROUTE,
            PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml",
        )
        self.assertEqual(
            self.module.DEFAULT_BASE_STAGE1_DIR,
            PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_s1forced",
        )
        self.assertEqual(self.module.parse_args([]).workers, 6)

    def test_validate_args_allows_six_workers_for_final_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            theta_csv = Path(tmp) / "all_evaluations.csv"
            theta_csv.write_text(
                "method,seed,round,parameter_id,t_lead,delta_T_thr,G_ext,Q_ratio,tau,score,final_status\n",
                encoding="utf-8",
            )
            args = self.module.parse_args([
                "--dry-run",
                "--theta-all-evaluations",
                str(theta_csv),
                "--workers",
                "6",
                "--run-id",
                "workers_six_validation",
            ])

            self.module.validate_args(args)

        self.assertEqual(args.workers, 6)

    def test_planned_task_rows_are_61_per_candidate(self):
        candidates = [{"route_id": "FINAL_DEST_A", "candidate_rank": 1, "route_xml": "route.xml", "stage1_dir": "stage1"}]
        departures = {"FINAL_DEST_A": [550.0 + index for index in range(30)]}
        rows = self.module.planned_task_rows(candidates, departures, PROJECT_ROOT / "runs/tmp_final_validation", phase=self.module.PHASE_FINAL)
        self.assertEqual(len(rows), 61)
        self.assertEqual(sum(row["mode"] == self.module.B004_MODE for row in rows), 1)
        self.assertEqual(sum(row["mode"] == self.module.B04_MODE for row in rows), 30)
        self.assertEqual(sum(row["mode"] == self.module.B4_MODE for row in rows), 30)
        self.assertEqual({row["phase"] for row in rows}, {self.module.PHASE_FINAL})

    def test_screening_and_final_task_counts_match_protocol(self):
        candidates = [
            {"route_id": f"FINAL_DEST_{index}", "candidate_rank": index, "route_xml": "route.xml", "stage1_dir": "stage1"}
            for index in range(1, 19)
        ]
        screening_departures = {candidate["route_id"]: [600.0] for candidate in candidates}
        screening_rows = self.module.planned_task_rows(candidates, screening_departures, PROJECT_ROOT / "runs/tmp_screening", phase=self.module.PHASE_SCREENING)
        self.assertEqual(len(screening_rows), 18 * 3)

        final_candidates = candidates[:3]
        final_departures = {candidate["route_id"]: [550.0 + index for index in range(30)] for candidate in final_candidates}
        final_rows = self.module.planned_task_rows(final_candidates, final_departures, PROJECT_ROOT / "runs/tmp_final", phase=self.module.PHASE_FINAL)
        self.assertEqual(len(final_rows), 3 * 61)

    def test_adaptive_repeats_enabled_only_for_final_pilot_runs(self):
        args = self.module.parse_args(["--phase", "final", "--repeats", "30"])
        self.assertTrue(self.module.adaptive_final_repeats_enabled(args, self.module.PHASE_FINAL, args.repeats))

        smoke_args = self.module.parse_args(["--phase", "final", "--repeats", "1"])
        self.assertFalse(self.module.adaptive_final_repeats_enabled(smoke_args, self.module.PHASE_FINAL, smoke_args.repeats))

        disabled_args = self.module.parse_args(["--phase", "final", "--repeats", "30", "--disable-adaptive-repeats"])
        self.assertFalse(self.module.adaptive_final_repeats_enabled(disabled_args, self.module.PHASE_FINAL, disabled_args.repeats))

    def test_average_rows_are_exactly_three_modes(self):
        rows = [
            {"mode": self.module.B004_MODE, "T_free_EMV_sec": "100", "D_E_sec": "0", "objective_score": "0", "emergency_arrived": "True", "emergency_teleport": "False"},
            {"mode": self.module.B04_MODE, "T_actual_EMV_sec": "200", "D_E_sec": "100", "D_G_sec": "30", "objective_score": "2000", "emergency_arrived": "True", "emergency_teleport": "False", "stage3_preemption_count": "0", "stage2_hold_count": "0"},
            {"mode": self.module.B4_MODE, "T_actual_EMV_sec": "150", "D_E_sec": "50", "D_G_sec": "35", "objective_score": "1000", "emergency_arrived": "True", "emergency_teleport": "False", "stage3_preemption_count": "3", "stage2_hold_count": "2"},
        ]
        averages = self.module.average_rows(rows)
        self.assertEqual([row["mode"] for row in averages], [self.module.B004_MODE, self.module.B04_MODE, self.module.B4_MODE])
        self.assertEqual(len(averages), 3)

    def test_repeat_stability_rows_pair_b04_b4_repeats(self):
        rows = []
        for repeat in range(1, 7):
            rows.append({
                "mode": self.module.B04_MODE,
                "repeat_id": str(repeat),
                "T_actual_EMV_sec": str(200 + repeat),
            })
            rows.append({
                "mode": self.module.B4_MODE,
                "repeat_id": str(repeat),
                "T_actual_EMV_sec": str(150 + repeat),
                "D_E_sec": str(50 + repeat),
                "D_G_sec": str(35 + repeat / 10),
                "stage3_preemption_count": "3",
                "stage2_hold_count": "2",
            })

        stability = self.module.repeat_stability_rows("FINAL_DEST_A", rows)

        self.assertEqual({row["metric"] for row in stability}, {
            "B4_vs_B04_D_E_improvement_sec",
            "B4_D_E_sec",
            "B4_D_G_sec",
            "B4_intervention_count",
        })
        self.assertTrue(all(row["repeat_count"] == 6 for row in stability))
        self.assertTrue(all(row["spc_status"] in {"stable", "active"} for row in stability))

    def test_relative_error_metric_row_requires_more_repeats(self):
        values = [300.0 + (100.0 if index % 2 else -100.0) for index in range(30)]
        row = self.module.relative_error_metric_row(
            "FINAL_DEST_A",
            "B4_T_EMV_sec",
            values,
            pilot_repeat_count=30,
            confidence_level=0.95,
            relative_error_target=0.05,
            max_repeats=300,
        )

        self.assertEqual(row["status"], "NEEDS_MORE")
        self.assertGreater(int(row["required_repeats"]), 30)
        self.assertGreater(int(row["additional_repeats_required"]), 0)

    def test_relative_error_rows_include_improvement_metric(self):
        rows = []
        for repeat in range(1, 31):
            rows.append({
                "mode": self.module.B04_MODE,
                "repeat_id": str(repeat),
                "T_actual_EMV_sec": "300",
            })
            rows.append({
                "mode": self.module.B4_MODE,
                "repeat_id": str(repeat),
                "T_actual_EMV_sec": "250",
                "D_E_sec": "120",
                "D_G_sec": "30",
            })

        precision_rows = self.module.relative_error_rows(
            "FINAL_DEST_A",
            rows,
            pilot_repeat_count=30,
            confidence_level=0.95,
            relative_error_target=0.05,
            max_repeats=300,
        )

        self.assertEqual(
            {row["metric"] for row in precision_rows},
            {"B4_T_EMV_sec", "B4_D_E_sec", "B4_D_G_sec", "B4_vs_B04_D_E_improvement_sec"},
        )
        self.assertTrue(all(row["status"] == "PASS" for row in precision_rows))

    def test_final_simulation_result_rows_keep_only_required_columns(self):
        params = self.module.B4ThetaParams.from_row({
            "parameter_id": "theta_final",
            "t_lead": "30",
            "delta_T_thr": "70",
            "G_ext": "10",
            "Q_ratio": "0.65",
            "tau": "0.84",
        })
        rows = [
            {"mode": self.module.B04_MODE, "route_id": "FINAL_DEST_A", "repeat_id": "1"},
            {
                "phase": self.module.PHASE_FINAL,
                "mode": self.module.B4_MODE,
                "route_id": "FINAL_DEST_A",
                "source_route_id": "ER_ACC_A",
                "target_edge_id": "edgeA",
                "repeat_id": "1",
                "parameter_id": "theta_final",
                "D_E_sec": "40",
                "D_G_sec": "12",
                "objective_score": "37.454545",
                "T_free_EMV_sec": "100",
                "T_actual_EMV_sec": "140",
                "T_G_actual_mean_sec": "52",
                "T_G_free_mean_sec": "40",
                "stage2_hold_count": "2",
                "stage3_preemption_count": "3",
                "w_E": "10",
                "w_G": "1",
            },
        ]

        final_rows = self.module.final_simulation_result_rows(rows, params)

        self.assertEqual(len(final_rows), 1)
        self.assertEqual(list(final_rows[0]), self.module.FINAL_SIMULATION_FIELDS)
        self.assertEqual(final_rows[0]["output_D_E_sec"], "40")
        self.assertEqual(final_rows[0]["output_D_G_sec"], "12")
        fields = self.module.FINAL_SIMULATION_FIELDS
        self.assertEqual(fields[fields.index("score") - 3:fields.index("score")], ["weight_E", "weight_G", "weight_ratio"])
        self.assertEqual(final_rows[0]["stage2_on_count"], "2")
        self.assertEqual(final_rows[0]["stage3_on_count"], "3")

    def test_spc_metric_row_marks_insufficient_repeats(self):
        row = self.module.spc_metric_row("FINAL_DEST_A", "B4_vs_B04_D_E_improvement_sec", [10.0, 11.0, 12.0])

        self.assertEqual(row["spc_status"], "insufficient")
        self.assertEqual(row["repeat_count"], 3)

    def test_final_theta_loads_five_variable_best_pass_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "all_evaluations.csv"
            fields = ["method", "seed", "round", "parameter_id", "t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau", "score", "final_status"]
            rows = [
                {"method": "BO", "seed": "1", "round": "1", "parameter_id": "bad_fail", "t_lead": "10", "delta_T_thr": "20", "G_ext": "30", "Q_ratio": "0.10", "tau": "0.75", "score": "1", "final_status": "FAIL"},
                {"method": "Random Search", "seed": "1", "round": "2", "parameter_id": "rs_pass", "t_lead": "48", "delta_T_thr": "220", "G_ext": "9", "Q_ratio": "0.22", "tau": "0.77", "score": "246.67", "final_status": "PASS"},
                {"method": "BO", "seed": "1", "round": "3", "parameter_id": "bo_pass", "t_lead": "29", "delta_T_thr": "72", "G_ext": "10", "Q_ratio": "0.67", "tau": "0.85", "score": "214.59", "final_status": "PASS"},
            ]
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            params, provenance = self.module.load_final_b4_params(
                theta_latest=Path(tmp) / "unused_latest.json",
                theta_all_evaluations=path,
                theta_method="ALL",
            )

        self.assertEqual(params.parameter_id, "final_validation_locked_theta")
        self.assertEqual(params.t_lead, 29.0)
        self.assertEqual(params.delta_T_thr, 72.0)
        self.assertEqual(params.G_ext, 10.0)
        self.assertEqual(params.Q_ratio, 0.67)
        self.assertEqual(params.tau, 0.85)
        self.assertEqual(set(provenance["decision_variables_fixed"]), set(self.module.THETA_FIELDS))
        self.assertNotIn("alpha", provenance["decision_variables_fixed"])
        self.assertNotIn("Q_trig", provenance["decision_variables_fixed"])
        self.assertFalse(provenance["bayesian_optimization_executed_by_final_validation"])

    def test_selection_excludes_invalid_no_improvement_and_no_intervention(self):
        rows = [
            {
                "candidate_rank": "1",
                "route_id": "A",
                "selection_status": "CANDIDATE",
                "B4_vs_B04_D_E_improvement_sec": "30",
                "B04_D_E_mean_sec": "50",
                "intervention_mean": "2",
                "mainroad_length_ratio": "0.8",
                "legacy_spine_length_ratio": "0.7",
            },
            {
                "candidate_rank": "2",
                "route_id": "B",
                "selection_status": "EXCLUDED",
                "B4_vs_B04_D_E_improvement_sec": "100",
                "B04_D_E_mean_sec": "100",
                "intervention_mean": "10",
                "mainroad_length_ratio": "0.9",
                "legacy_spine_length_ratio": "0.9",
            },
            {
                "candidate_rank": "3",
                "route_id": "C",
                "selection_status": "CANDIDATE",
                "B4_vs_B04_D_E_improvement_sec": "40",
                "B04_D_E_mean_sec": "45",
                "intervention_mean": "1",
                "mainroad_length_ratio": "0.6",
                "legacy_spine_length_ratio": "0.6",
            },
        ]
        selected = self.module.select_final_candidates(rows, limit=2)
        self.assertEqual([row["route_id"] for row in selected], ["C", "A"])
        self.assertEqual([row["selection_rank"] for row in selected], [1, 2])


if __name__ == "__main__":
    unittest.main()
