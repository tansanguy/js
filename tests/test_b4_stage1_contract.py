#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "09 Compact Corridor Baseline/b4_stage1_pipeline.py"
STAGE1_DIR = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1"
B04_MANIFEST = PROJECT_ROOT / "configs/compact_v9_B04_b0_manifest.json"
CSV_SIGNAL_CANDIDATES = PROJECT_ROOT / "data_prepared/compact_v9/net/B04_csv_signal_candidates.csv"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("b4_stage1_pipeline_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class B4Stage1ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = load_pipeline()
        cls.manifest_before = B04_MANIFEST.read_text(encoding="utf-8")
        original_loader = cls.pipeline.load_b04_pipeline

        def guarded_loader():
            b04 = original_loader()
            b04.run_b0_candidate = lambda *_args, **_kwargs: cls.fail("B4 Stage 1 must not run SUMO")
            b04.run_b0_all = lambda *_args, **_kwargs: cls.fail("B4 Stage 1 must not run SUMO")
            b04.build_demand = lambda *_args, **_kwargs: cls.fail("B4 Stage 1 must not build new demand")
            return b04

        cls.pipeline.load_b04_pipeline = guarded_loader
        try:
            cls.summary = cls.pipeline.build_b4_stage1()
        finally:
            cls.pipeline.load_b04_pipeline = original_loader
        cls.manifest_after = B04_MANIFEST.read_text(encoding="utf-8")

    def test_outputs_are_b4_named_and_manifest_is_unchanged(self):
        self.assertEqual(self.manifest_before, self.manifest_after)
        self.assertEqual(self.summary["primary_candidate"], "B04_ad_variance_smoothed")
        self.assertEqual(self.summary["manifest_selected_candidate"], "B04_ad_variance_smoothed")
        self.assertEqual(self.summary["manifest_selected_candidate_role"], "primary_selected")
        metrics = self.summary["primary_candidate_lock"]["metrics"]
        self.assertEqual(metrics["vehicles"], 1302)
        self.assertEqual(metrics["main_through_flow"], 657)
        self.assertEqual(metrics["terminal_sink_flow"], 0)
        self.assertAlmostEqual(metrics["top_sink_share"], 0.141321)
        self.assertAlmostEqual(metrics["speed_mae_kmh"], 11.533)
        self.assertEqual(metrics["free_count"], 10)
        self.assertEqual(metrics["od_undercovered"], 10)
        self.assertEqual(metrics["queue_not_forming"], 0)
        self.assertEqual(metrics["teleport"], 0)
        self.assertEqual(metrics["arrived_ratio"], 1.0)
        expected = {
            "b4_route_movement_plan_json",
            "b4_intersections_csv",
            "b4_approach_storage_link_plan_csv",
            "b4_merge_zone_json",
            "b4_departure_flow_plan_json",
            "b4_bottleneck_queue_readiness_csv",
            "b4_case_b_candidates_csv",
            "b4_case_b_candidates_json",
            "b4_control_queue_threshold_proposal_json",
            "b4_b0_measured_signal_params_csv",
            "b4_ta_proxy_policy_json",
            "b4_stage2_b0_merge_hold_params_json",
            "b4_stage2_b0_merge_hold_params_csv",
            "b4_runtime_index_json",
            "b4_stage1_summary_json",
            "b4_stage1_review_html",
        }
        self.assertTrue(expected.issubset(self.summary["outputs"]))
        for rel_path in self.summary["outputs"].values():
            path = PROJECT_ROOT / rel_path
            self.assertTrue(path.is_file(), rel_path)
            self.assertNotIn("b3_", path.name.lower())

    def test_approach_storage_plan_separates_local_and_corridor_storage(self):
        path = STAGE1_DIR / "b4_approach_storage_link_plan.csv"
        with path.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertGreater(len(rows), 0)
        required = {
            "movement_id",
            "tls_id",
            "from_edge",
            "to_edge",
            "linkIndex",
            "control_linkIndex",
            "ev_route_linkIndex",
            "parallel_through_linkIndex",
            "same_lane_blocking_linkIndex",
            "flush_linkIndex",
            "selected_flush_phase",
            "control_strategy",
            "approach_lanes",
            "storage_edges",
            "storage_lanes",
            "stopline_local_storage_m",
            "corridor_storage_length_m",
            "lane_count",
            "selected_green_phase",
            "selected_red_phase",
            "mapped_S_segment",
            "route_order_index",
            "controllable",
            "linkIndex_note",
        }
        self.assertTrue(required.issubset(rows[0].keys()))
        for row in rows:
            self.assertEqual(float(row["stopline_local_storage_m"]), 100.0)
            self.assertLessEqual(float(row["corridor_storage_length_m"]), 250.0)
            self.assertIn("SUMO TLS movement index", row["linkIndex_note"])
            self.assertTrue(row["movement_id"].startswith("B4_MOVEMENT_"))

    def test_mainline_same_lane_blocker_flush_plan_is_recorded(self):
        path = STAGE1_DIR / "b4_approach_storage_link_plan.csv"
        with path.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        movement = next(
            row for row in rows
            if row["from_edge"] == "781985787#0" and row["to_edge"] == "218915135#3"
        )
        self.assertEqual(movement["from_edge"], "781985787#0")
        self.assertEqual(movement["to_edge"], "218915135#3")
        self.assertEqual(movement["ev_route_linkIndex"], "18")
        self.assertEqual(movement["parallel_through_linkIndex"], "14 15 16 18")
        self.assertEqual(movement["same_lane_blocking_linkIndex"], "15 16 17")
        self.assertEqual(movement["flush_linkIndex"], "15 16 17")
        self.assertEqual(movement["selected_green_phase"], "4")
        self.assertEqual(movement["selected_flush_phase"], "2")
        self.assertEqual(movement["full_through_phase_available"], "False")
        self.assertEqual(movement["same_lane_blocker_flush_available"], "True")
        self.assertEqual(movement["control_strategy"], "route_green_with_same_lane_blocker_flush")

    def test_readiness_keeps_80_100_120_fill_and_uses_100m_trigger(self):
        path = STAGE1_DIR / "b4_bottleneck_queue_readiness.csv"
        with path.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertGreater(len(rows), 0)
        required = {
            "local_fill_80m",
            "local_fill_100m",
            "local_fill_120m",
            "stopline_local_fill_100m",
            "corridor_fill_250m",
            "approach_speed_kmh",
            "control_candidate",
            "trigger_reason",
        }
        self.assertTrue(required.issubset(rows[0].keys()))
        for row in rows:
            self.assertEqual(row["local_fill_100m"], row["stopline_local_fill_100m"])
            local_100 = float(row["local_fill_100m"])
            speed = float(row["approach_speed_kmh"])
            expected = local_100 >= 0.50 or (0.0 < speed <= 15.0)
            self.assertEqual(row["control_candidate"], str(expected))

    def test_threshold_proposal_declares_primary_metric_and_event_schema(self):
        proposal = json.loads((STAGE1_DIR / "b4_control_queue_threshold_proposal.json").read_text(encoding="utf-8"))
        self.assertEqual(proposal["primary_control_fill_metric"], "stopline_local_fill_100m")
        self.assertEqual(proposal["thresholds"]["local_fill_trigger"], 0.50)
        self.assertEqual(proposal["thresholds"]["speed_trigger_kmh"], 15.0)
        self.assertEqual(
            proposal["control_candidate_expression"],
            "stopline_local_fill_100m >= 0.50 OR approach_speed_kmh <= 15",
        )
        self.assertEqual(
            proposal["local_fill_comparison_metrics"],
            ["local_fill_80m", "local_fill_100m", "local_fill_120m"],
        )
        for field in ["time", "stage", "action_type", "tls_id", "movement_id", "local_fill_100m", "safety_status"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["TA_proxy_sec", "tQ_sec", "b0_q_avg_proxy_veh", "ta_triggered", "ta_formula"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["queue_source", "case_b_source", "tS_source", "TA_case", "TA_upstream_sec", "TA_bottleneck_sec"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["case_b_mapping_status", "case_b_segment_id", "case_b_segment_queue_m_proxy", "case_b_segment_fill", "case_b_same_tls_policy"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["n_occ_runtime_veh", "T_hold_proxy_sec", "stage2_formula", "stage2_measurement_source"]:
            self.assertIn(field, proposal["event_schema"])
        self.assertIn("Lq_merge_m", proposal["event_schema"])
        self.assertEqual(proposal["decision_variables"], ["alpha", "t_lead", "delta_T_thr", "G_ext", "Q_trig"])
        self.assertEqual(proposal["decision_variable_screening"]["decision_variables_X"], ["alpha", "t_lead", "delta_T_thr", "G_ext", "Q_trig"])
        self.assertEqual(
            proposal["runtime_preemption_expression"],
            "(stopline_local_fill_100m >= 0.50 OR approach_speed_kmh <= 15) AND TA_proxy_sec <= 0",
        )

    def test_b0_measured_signal_params_and_ta_policy_are_generated(self):
        measured_path = STAGE1_DIR / "b4_b0_measured_signal_params.csv"
        with measured_path.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertGreater(len(rows), 0)
        required = {
            "movement_id",
            "q_avg_b0_proxy_veh",
            "q_max_b0_proxy_veh",
            "tQ_hist_b0_sec",
            "lambda_b0_vph",
            "L_local_m",
            "L_corridor_m",
            "C_local_proxy_veh",
            "queue_calibration_reference_m",
            "queue_calibration_measured_m",
            "queue_calibration_factor",
            "queue_calibration_factor_applied",
            "queue_calibration_source",
            "measurement_source",
            "field_queue_claim",
        }
        self.assertTrue(required.issubset(rows[0].keys()))
        for row in rows:
            self.assertEqual(row["measurement_source"], "SUMO_B04_AD_B0_edge_lane_data")
            self.assertEqual(row["field_queue_claim"], "false")
            self.assertEqual(float(row["L_local_m"]), 100.0)
            self.assertLessEqual(float(row["L_corridor_m"]), 250.0)
            self.assertGreaterEqual(float(row["C_local_proxy_veh"]), 0.0)
            self.assertGreater(float(row["queue_calibration_factor_applied"]), 0.0)
            self.assertEqual(row["queue_calibration_source"], "b4_bottleneck_queue_readiness.csv/b4_b0_measured_signal_params.csv")
        policy = json.loads((STAGE1_DIR / "b4_ta_proxy_policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["ta_formula"], "TA_proxy_sec = tE_sec - tS_sec - tQ_sec")
        self.assertIn("TA_proxy_sec <= 0", policy["ta_control_policy"])
        self.assertFalse(policy["field_queue_claim"])
        self.assertIn("b0_source_policy", policy)

    def test_case_b_candidates_are_generated_with_mapping_status(self):
        csv_path = STAGE1_DIR / "b4_case_b_candidates.csv"
        with csv_path.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual({row["segment_id"] for row in rows}, {"S7", "S10", "S11"})
        required = {
            "segment_id",
            "bottleneck_movement_id",
            "upstream_movement_id",
            "L_b0_m",
            "lane_drop_delta",
            "q_avg_B0",
            "q_max_B0",
            "tQ_hist_B0",
            "lambda_B0",
            "fill_B0",
            "speed_B0",
            "segment_q_avg_B0",
            "segment_q_max_B0",
            "segment_tQ_hist_B0",
            "segment_lambda_B0",
            "segment_fill_B0",
            "segment_speed_B0",
            "mapping_status",
            "segment_edges",
            "segment_lanes",
            "segment_route_start_index",
            "segment_route_end_index",
            "proxy_edge_gap_upstream",
            "proxy_edge_gap_bottleneck",
            "same_tls_chain",
            "case_b_runtime_enabled",
            "case_b_prior_risk",
        }
        self.assertTrue(required.issubset(rows[0].keys()))
        expected = {
            "S7": ("B4_MOVEMENT_05", "B4_MOVEMENT_04", "mapped_exact"),
            "S10": ("B4_MOVEMENT_07", "B4_MOVEMENT_06", "mapped_route_span_proxy"),
            "S11": ("B4_MOVEMENT_08", "B4_MOVEMENT_07", "mapped_exact"),
        }
        for row in rows:
            bottleneck_id, upstream_id, mapping_status = expected[row["segment_id"]]
            self.assertEqual(row["mapping_status"], mapping_status)
            self.assertEqual((row["bottleneck_movement_id"], row["upstream_movement_id"]), (bottleneck_id, upstream_id))
            self.assertEqual(row["case_b_runtime_enabled"], "True")
            self.assertNotEqual(row["segment_edges"], "")
            self.assertNotEqual(row["segment_lanes"], "")
            self.assertGreater(float(row["L_b0_m"]), 0.0)
            self.assertGreaterEqual(int(row["lane_drop_delta"]), -1)

        payload = json.loads((STAGE1_DIR / "b4_case_b_candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["measurement_source"], "SUMO B04 no-control B0 measured proxy")
        self.assertIn("source_policy", payload)
        self.assertEqual(payload["mapped_count"], 3)
        self.assertEqual(payload["runtime_enabled_count"], 3)

    def test_stage2_b0_merge_hold_params_are_generated(self):
        payload = json.loads((STAGE1_DIR / "b4_stage2_b0_merge_hold_params.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["measurement_source"], "SUMO_B04_AD_B0_laneData_edgeData_proxy")
        self.assertFalse(payload["field_measurement_claim"])
        self.assertTrue(payload["runtime_control_uses_formula_directly"])
        params = payload["params"]
        self.assertEqual(params["merge_control_tls"], "COMPACT_V9_FIRE_STATION_ENTRY_TLS")
        self.assertAlmostEqual(float(params["L_merge_m"]), 50.0)
        self.assertAlmostEqual(float(params["C_merge_proxy_veh"]), 50.0 / 6.5, places=5)
        self.assertAlmostEqual(float(params["n_need_proxy_veh"]), 2.0)
        self.assertEqual(params["measurement_source"], "SUMO_B04_AD_B0_laneData_edgeData_proxy")
        self.assertGreaterEqual(float(params["D_merge_m"]), 0.0)
        self.assertGreaterEqual(float(params["b0_merge_n_occ_mean_proxy_veh"]), 0.0)

        with (STAGE1_DIR / "b4_stage2_b0_merge_hold_params.csv").open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field_measurement_claim"], "false")

    def test_departure_flow_records_entry_tls_hold_and_ev_warn(self):
        departure = json.loads((STAGE1_DIR / "b4_departure_flow_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(departure["fire_station_start_edge"], "420331801#1")
        self.assertEqual(departure["mainline_target_edge"], "-174870621#8")
        self.assertEqual(departure["merge_control_tls"], "COMPACT_V9_FIRE_STATION_ENTRY_TLS")
        self.assertEqual(departure["background_inflow_red_hold_phase"], 2)
        self.assertEqual(departure["merge_control_linkIndex"], "0 4 5 6")
        self.assertEqual(departure["ev_release_control_status"], "uncontrolled_by_merge_tls")
        self.assertEqual(departure["validation"]["ev_release_control"], "WARN")
        self.assertEqual(departure["dispatch_lead_time_sec"], 35.0)

    def test_created_csv_tls_are_connected_to_b4_runtime(self):
        with CSV_SIGNAL_CANDIDATES.open(encoding="utf-8-sig", newline="") as file:
            candidates = [row for row in csv.DictReader(file) if row["action"] == "created_tls"]
        self.assertEqual(len(candidates), 11)

        with (STAGE1_DIR / "b4_intersections.csv").open(encoding="utf-8-sig", newline="") as file:
            intersections = {row["tls_id"]: row for row in csv.DictReader(file)}
        runtime = json.loads((STAGE1_DIR / "b4_runtime_index.json").read_text(encoding="utf-8"))
        movements_by_tls: dict[str, list[dict[str, object]]] = {}
        for movement in runtime["ordered_movements"]:
            movements_by_tls.setdefault(str(movement["tls_id"]), []).append(movement)

        green_only_tls = set()
        for candidate in candidates:
            tls_id = candidate["tls_id"]
            self.assertIn(tls_id, intersections)
            self.assertGreater(int(intersections[tls_id]["controllable_count"]), 0)
            movements = movements_by_tls.get(tls_id, [])
            self.assertTrue(movements, tls_id)
            self.assertTrue(any(movement["controllable"] for movement in movements), tls_id)
            controllable = next(movement for movement in movements if movement["controllable"])
            self.assertNotEqual(controllable["selected_green_phase"], "")
            self.assertTrue(controllable["control_link_indices"])
            if int(candidate["phase_count"]) == 1:
                green_only_tls.add(tls_id)
                self.assertFalse(controllable["red_phase_available"])
                self.assertTrue(controllable["green_only_no_red_phase"])
                self.assertEqual(controllable["selected_red_phase"], "")
            else:
                self.assertTrue(controllable["red_phase_available"], tls_id)

        self.assertEqual(green_only_tls, {
            "CSV_TLS_S11_S12_TOEGYE_RO_2_GA",
            "CSV_TLS_S14_S15_DAYS_HOTEL_FRONT",
            "CSV_TLS_S18_S19_SAMSEON_BUILDING",
        })

    def test_runtime_index_is_consumed_by_b4_runtime(self):
        runtime = json.loads((STAGE1_DIR / "b4_runtime_index.json").read_text(encoding="utf-8"))
        self.assertFalse(runtime.get("runtime_implemented", False))
        self.assertEqual(runtime["runtime_status"], "stage1_static_index_consumed_by_b4_runtime")
        self.assertEqual(runtime["decision_variables"], ["alpha", "t_lead", "delta_T_thr", "G_ext", "Q_trig"])
        self.assertEqual(runtime["max_active_movements"], 3)
        self.assertIn("Use only precomputed Stage 1 lanes", runtime["scan_policy"])


if __name__ == "__main__":
    unittest.main()
