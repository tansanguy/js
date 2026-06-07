#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "09 Compact Corridor Baseline/b4_stage1_pipeline.py"
STAGE1_DIR = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_s1forced"
B04_MANIFEST = PROJECT_ROOT / "configs/compact_v9_B04_b0_manifest.json"
CSV_SIGNAL_CANDIDATES = PROJECT_ROOT / "data_prepared/compact_v9/net/B04_csv_signal_candidates.csv"
B04_PRIMARY_VALIDATION = PROJECT_ROOT / "results/metrics/compact_v9_B04/B04_ad_stage23_trigger/B04_validation_summary.json"
B04_PRIMARY_DEMAND = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"


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
        self.assertEqual(self.summary["primary_candidate"], "B04_ad_stage23_trigger")
        self.assertEqual(self.summary["manifest_selected_candidate"], "B04_ad_stage23_trigger")
        self.assertEqual(self.summary["manifest_selected_candidate_role"], "primary_selected")
        self.assertEqual(self.summary["provenance_status"], "PASS")
        self.assertFalse(self.summary["allow_runtime_input_override"])
        metrics = self.summary["primary_candidate_lock"]["metrics"]
        validation = json.loads(B04_PRIMARY_VALIDATION.read_text(encoding="utf-8"))
        run = validation["run_summary"]
        vehicle_count = sum(1 for child in ET.parse(B04_PRIMARY_DEMAND).getroot() if child.tag == "vehicle")
        self.assertEqual(self.summary["primary_candidate_lock"]["measurement_source_candidate"], "B04_ad_stage23_trigger")
        self.assertEqual(metrics["candidate"], "B04_ad_stage23_trigger")
        self.assertEqual(metrics["vehicles"], vehicle_count)
        self.assertEqual(metrics["main_through_flow"], 473)
        self.assertEqual(metrics["terminal_sink_flow"], 140)
        self.assertGreater(metrics["top_sink_share"], 0.0)
        self.assertAlmostEqual(metrics["speed_mae_kmh"], validation["speed_mae_kmh"])
        self.assertEqual(metrics["free_count"], validation["free_count"])
        self.assertEqual(metrics["od_undercovered"], validation["free_flow_od_audit"]["od_undercovered_count"])
        self.assertEqual(metrics["queue_not_forming"], validation["free_flow_od_audit"]["queue_not_forming_count"])
        self.assertEqual(metrics["teleport"], run["background_teleported"])
        self.assertEqual(metrics["emergency_arrived"], run["emergency_arrived"])
        self.assertEqual(metrics["emergency_teleport"], run["emergency_teleport"])
        self.assertEqual(metrics["stage23_teleported"], run["stage23_teleported"])
        self.assertEqual(metrics["base_background_teleported"], run["base_background_teleported"])
        self.assertEqual(metrics["arrived_ratio"], validation["background_arrived_ratio"])
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

    def assert_phase_has_green(self, tls_id: str, phase_value: str, link_index_text: str) -> None:
        self.assertNotEqual(phase_value, "")
        self.assertNotEqual(link_index_text, "")
        phases = self.pipeline.tl_logic_details(self.pipeline.B04_NET)[tls_id]
        phase_index = int(phase_value)
        self.assertLess(phase_index, len(phases), tls_id)
        state = str(phases[phase_index]["state"])
        for link_index in [int(value) for value in link_index_text.split()]:
            self.assertLess(link_index, len(state), tls_id)
            self.assertIn(state[link_index], {"G", "g"}, (tls_id, phase_index, link_index, state))

    def test_approach_storage_plan_separates_local_and_corridor_storage(self):
        path = STAGE1_DIR / "b4_approach_storage_link_plan.csv"
        with path.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertGreater(len(rows), 0)
        required = {
            "movement_id",
            "tls_id",
            "route_intersection_index",
            "from_edge",
            "to_edge",
            "L_m",
            "W_m",
            "Q_th_formula",
            "Q_th_default_m",
            "is_merge",
            "stage_owner",
            "ped_min_green_sec",
            "ped_min_green_source",
            "ped_safety_margin_sec",
            "Gm_sec",
            "Y_sec",
            "R_sec",
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
            self.assertAlmostEqual(float(row["Q_th_default_m"]), 0.0)
            self.assertGreater(float(row["ped_min_green_sec"]), 0.0)
            self.assertTrue(row["movement_id"].startswith("B4_MOVEMENT_"))
        merge_rows = [row for row in rows if row["is_merge"] == "True"]
        self.assertEqual(len(merge_rows), 1)
        self.assertEqual(merge_rows[0]["stage_owner"], "stage2_merge")

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
        self.assert_phase_has_green(movement["tls_id"], movement["selected_green_phase"], movement["ev_route_linkIndex"])
        self.assert_phase_has_green(movement["tls_id"], movement["selected_flush_phase"], movement["flush_linkIndex"])
        self.assertNotEqual(movement["selected_green_phase"], movement["selected_flush_phase"])
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
            "EVTSP Stage3 scans all controllable route movements ahead of EV; queue/speed readiness remains diagnostic only.",
        )
        self.assertEqual(
            proposal["local_fill_comparison_metrics"],
            ["local_fill_80m", "local_fill_100m", "local_fill_120m"],
        )
        for field in ["time", "stage", "action_type", "tls_id", "movement_id", "local_fill_100m", "safety_status"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["Q_ratio", "Q_th_m", "Q_th_merge_m", "T_hold_sec", "hold_elapsed_sec"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["route_intersection_index", "L_m", "W_m", "Gm_sec", "Y_sec", "R_sec", "green_dur_sec"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["intersection_index", "junction_id", "is_ahead_of_ev", "is_i_merge", "L", "W", "Lq", "tau", "tau_times_L"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["case_type", "downstream_index", "gate_target", "tE_gate_target", "delta_T_thr", "gate_result", "ge", "tQ"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["t_lead", "G_ext", "preemption_state", "processing_order", "Lq_i", "TA_down", "tQ_i"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["TA_proxy_sec", "tQ_sec", "b0_q_avg_proxy_veh", "ta_triggered", "ta_formula"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["queue_source", "case_b_source", "tS_source", "TA_case", "TA_upstream_sec", "TA_bottleneck_sec"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["case_b_mapping_status", "case_b_segment_id", "case_b_segment_queue_m_proxy", "case_b_segment_fill", "case_b_same_tls_policy"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["n_occ_runtime_veh", "T_hold_proxy_sec", "stage2_formula", "stage2_measurement_source"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["step", "ev_status", "EV_NotDeparted", "EV_Departed", "EV_MergePassed"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["time_to_merge_sec", "time_to_merge_source", "s_vph", "HOLD_MAX_sec", "current_phase", "current_state"]:
            self.assertIn(field, proposal["event_schema"])
        for field in ["ped_state", "SafetyGate_result", "action", "deny_reason"]:
            self.assertIn(field, proposal["event_schema"])
        self.assertIn("Lq_merge_m", proposal["event_schema"])
        self.assertEqual(proposal["decision_variables"], ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"])
        self.assertEqual(proposal["decision_variable_screening"]["decision_variables_X"], ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"])
        self.assertEqual(
            proposal["runtime_preemption_expression"],
            "Scan all ahead movements after EV departure; skip i_merge; CaseA uses [i], CaseB uses adjacent [i+1,i] when Lq_i >= tau * L_i; gate by tE_gate_target <= delta_T_thr and TA <= t_lead.",
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
        self.assertEqual(policy["ta_formula"], "TA_sec = tE_sec - (Y_sec + R_sec + max(0, Gm_sec - elapsed_green_sec)) - tQ_sec")
        self.assertIn("TA_sec <= t_lead", policy["ta_control_policy"])
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
            "S10": ("B4_MOVEMENT_08", "B4_MOVEMENT_07", "mapped_route_span_proxy"),
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
        self.assertEqual(payload["duplicate_movement_pair_count"], 1)
        self.assertEqual(payload["duplicate_movement_pairs"][0]["segments"], ["S10", "S11"])
        self.assertIn("largest live segment fill", payload["runtime_duplicate_policy"])
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
        self.assertAlmostEqual(float(params["len_E_m"]), 8.0)
        self.assertEqual(params["len_E_source"], "firetruck_route_vType_length")
        self.assertAlmostEqual(float(params["n_need_proxy_veh"]), 3.0)
        self.assertEqual(params["measurement_source"], "SUMO_B04_AD_B0_laneData_edgeData_proxy")
        self.assertGreaterEqual(float(params["D_merge_m"]), 0.0)
        self.assertEqual(float(params["t_dispatch_delay_sec"]), 45.0)
        self.assertEqual(float(params["tE_merge_sec"]), 10.0)
        self.assertEqual(params["Q_th_merge_formula"], "Q_ratio * L_merge_m")
        self.assertAlmostEqual(float(params["Q_th_merge_default_m"]), 0.0)
        self.assertEqual(params["HOLD_MAX_formula"], "max(ped_min_green_sec - ped_safety_margin_sec, 1)")
        self.assertIn("HOLD_MAX_sec", params)
        self.assertAlmostEqual(float(params["ped_min_green_sec"]), 17.0)
        self.assertAlmostEqual(float(params["ped_safety_margin_sec"]), 3.0)
        self.assertAlmostEqual(float(params["HOLD_MAX_sec"]), 14.0)
        self.assertGreaterEqual(float(params["b0_merge_n_occ_mean_proxy_veh"]), 0.0)

        with (STAGE1_DIR / "b4_stage2_b0_merge_hold_params.csv").open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field_measurement_claim"], "false")

    def test_stage1_audit_records_evtsp_initialize_contract(self):
        runtime_index = json.loads((STAGE1_DIR / "b4_runtime_index.json").read_text(encoding="utf-8"))
        audit = runtime_index["stage1_audit"]
        self.assertEqual(audit["schema"], "compact_v9_B4_evtsp_stage1_audit.v1")
        self.assertEqual(audit["dispatch_time_rel_sec"], 0.0)
        self.assertEqual(audit["dispatch_time_abs_sec"], 61200.0)
        self.assertEqual(audit["sim_begin_abs_sec"], 61200.0)
        self.assertEqual(audit["sim_end_abs_sec"], 64800.0)
        self.assertEqual(audit["step_length_sec"], 1.0)
        self.assertEqual(audit["t_dispatch_delay_sec"], 45.0)
        self.assertEqual(audit["tE_merge_sec"], 10.0)
        self.assertEqual(audit["t_merge_abs_rel_sec"], 55.0)
        self.assertEqual(audit["Q_ratio_default"], 0.0)
        self.assertEqual(audit["tau_default"], 0.75)
        self.assertEqual(audit["tau_bounds"], [0.7, 0.9])
        self.assertEqual(audit["Q_ratio_bounds"], [0.0, 1.0])
        self.assertEqual(audit["len_E_m"], 8.0)
        self.assertEqual(audit["len_E_source"], "firetruck_route_vType_length")
        self.assertGreaterEqual(audit["i_merge"], 1)
        self.assertLessEqual(audit["i_merge"], audit["N"])
        self.assertEqual(runtime_index["i_merge"], audit["i_merge"])

    def test_departure_flow_records_entry_tls_hold_and_ev_warn(self):
        departure = json.loads((STAGE1_DIR / "b4_departure_flow_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(departure["fire_station_start_edge"], "420331801#1")
        self.assertEqual(departure["mainline_target_edge"], "-174870621#8")
        self.assertEqual(departure["merge_control_tls"], "COMPACT_V9_FIRE_STATION_ENTRY_TLS")
        self.assertEqual(departure["background_inflow_red_hold_phase"], 2)
        self.assertEqual(departure["merge_control_linkIndex"], "0 4 5 6")
        self.assertEqual(departure["ev_release_control_status"], "uncontrolled_by_merge_tls")
        self.assertEqual(departure["validation"]["ev_release_control"], "WARN")
        self.assertEqual(departure["dispatch_lead_time_sec"], 45.0)

    def test_created_csv_tls_are_connected_to_b4_runtime(self):
        with CSV_SIGNAL_CANDIDATES.open(encoding="utf-8-sig", newline="") as file:
            candidates = [row for row in csv.DictReader(file) if row["action"] == "created_tls"]
        self.assertEqual(len(candidates), 13)

        with (STAGE1_DIR / "b4_intersections.csv").open(encoding="utf-8-sig", newline="") as file:
            intersections = {row["tls_id"]: row for row in csv.DictReader(file)}
        runtime = json.loads((STAGE1_DIR / "b4_runtime_index.json").read_text(encoding="utf-8"))
        movements_by_tls: dict[str, list[dict[str, object]]] = {}
        for movement in runtime["ordered_movements"]:
            movements_by_tls.setdefault(str(movement["tls_id"]), []).append(movement)

        green_only_tls = set()
        unlinked_created_tls = set()
        for candidate in candidates:
            tls_id = candidate["tls_id"]
            if tls_id not in intersections:
                unlinked_created_tls.add(tls_id)
                continue
            self.assertGreater(int(intersections[tls_id]["controllable_count"]), 0)
            movements = movements_by_tls.get(tls_id, [])
            if not movements:
                unlinked_created_tls.add(tls_id)
                continue
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
        })
        self.assertEqual(unlinked_created_tls, set())

    def test_runtime_index_is_consumed_by_b4_runtime(self):
        runtime = json.loads((STAGE1_DIR / "b4_runtime_index.json").read_text(encoding="utf-8"))
        self.assertFalse(runtime.get("runtime_implemented", False))
        self.assertEqual(runtime["runtime_status"], "stage1_static_index_consumed_by_b4_runtime")
        self.assertEqual(runtime["decision_variables"], ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"])
        self.assertEqual(runtime["decision_variable_bounds"]["Q_ratio"]["lower"], 0.0)
        self.assertEqual(runtime["decision_variable_bounds"]["Q_ratio"]["upper"], 1.0)
        self.assertEqual(runtime["decision_variable_bounds"]["tau"]["lower"], 0.70)
        self.assertEqual(runtime["decision_variable_bounds"]["tau"]["upper"], 0.90)
        self.assertEqual(runtime["max_active_movements"], 3)
        self.assertIn("Stage3 scans only precomputed route movements ahead of EV", runtime["scan_policy"])


if __name__ == "__main__":
    unittest.main()
