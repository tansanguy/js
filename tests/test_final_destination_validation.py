#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
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

    def test_planned_task_rows_are_61_per_candidate(self):
        candidates = [{"route_id": "FINAL_DEST_A", "candidate_rank": 1, "route_xml": "route.xml", "stage1_dir": "stage1"}]
        departures = {"FINAL_DEST_A": [550.0 + index for index in range(30)]}
        rows = self.module.planned_task_rows(candidates, departures, PROJECT_ROOT / "runs/tmp_final_validation")
        self.assertEqual(len(rows), 61)
        self.assertEqual(sum(row["mode"] == self.module.B004_MODE for row in rows), 1)
        self.assertEqual(sum(row["mode"] == self.module.B04_MODE for row in rows), 30)
        self.assertEqual(sum(row["mode"] == self.module.B4_MODE for row in rows), 30)

    def test_average_rows_are_exactly_three_modes(self):
        rows = [
            {"mode": self.module.B004_MODE, "T_free_EMV_sec": "100", "d_EMV_sec": "0", "objective_score": "0", "emergency_arrived": "True", "emergency_teleport": "False"},
            {"mode": self.module.B04_MODE, "T_actual_EMV_sec": "200", "d_EMV_sec": "100", "objective_score": "2000", "general_mean_travel_time_sec": "30", "emergency_arrived": "True", "emergency_teleport": "False", "stage3_preemption_count": "0", "stage2_hold_count": "0"},
            {"mode": self.module.B4_MODE, "T_actual_EMV_sec": "150", "d_EMV_sec": "50", "objective_score": "1000", "general_mean_travel_time_sec": "35", "emergency_arrived": "True", "emergency_teleport": "False", "stage3_preemption_count": "3", "stage2_hold_count": "2"},
        ]
        averages = self.module.average_rows(rows)
        self.assertEqual([row["mode"] for row in averages], [self.module.B004_MODE, self.module.B04_MODE, self.module.B4_MODE])
        self.assertEqual(len(averages), 3)

    def test_locked_params_read_existing_result_without_bo_execution(self):
        payload = {
            "lock_status": "TEST_LOCK",
            "decision_variables_fixed": {
                "alpha": 1.15,
                "t_lead": 21.0,
                "delta_T_thr": 80.0,
                "G_ext": 32.0,
                "Q_trig": 0.0,
            },
            "selected_structure": {
                "tau": 0.85,
                "hold_max": 33.0,
                "d_up": 3,
                "tau_scale": 0.8,
                "tau_numerator_gamma": 5.0,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            params, provenance = self.module.load_locked_b4_params(path)
        self.assertEqual(params.parameter_id, "final_validation_locked_bo_result")
        self.assertEqual(params.alpha, 1.15)
        self.assertEqual(params.t_lead, 21.0)
        self.assertEqual(params.delta_T_thr, 80.0)
        self.assertEqual(params.G_ext, 32.0)
        self.assertEqual(params.Q_trig, 0.0)
        self.assertFalse(provenance["bayesian_optimization_executed_by_final_validation"])


if __name__ == "__main__":
    unittest.main()
