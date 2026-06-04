#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "09 Compact Corridor Baseline/b04_baseline_pipeline.py"
MANIFEST = PROJECT_ROOT / "configs/compact_v9_B04_b0_manifest.json"
TARGET_PROFILE = PROJECT_ROOT / "data_prepared/compact_v9/map/B04_target_profile.csv"
SELECTION = PROJECT_ROOT / "results/metrics/compact_v9_B04/selected/selection_summary.json"
REVIEW_HTML = PROJECT_ROOT / "results/html/compact_v9_B04_demand_validation_review.html"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("b04_baseline_pipeline_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CompactV9B04BaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = load_pipeline()

    def test_manifest_uses_b04_green18_net(self):
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(payload["baseline_name"], "B04")
        self.assertEqual(payload["mode"], "B0")
        self.assertEqual(payload["parameter_id"], "no_control")
        self.assertEqual(payload["active_net"], "data_prepared/compact_v9/net/jungbu_compact_v9_B04_green18.net.xml")
        self.assertIn("green18", payload["green18_source_net"])
        self.assertIn("background_routes_compact_v9_B04_", payload["background_route"])

    def test_target_profile_has_22_segments_both_directions(self):
        with TARGET_PROFILE.open(encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 44)
        self.assertEqual({row["direction"] for row in rows}, {"upbound", "downbound"})
        self.assertEqual({row["segment_id"] for row in rows}, {f"S{i}" for i in range(1, 23)})
        for row in rows:
            self.assertGreater(float(row["reference_volume_vph"]), 0.0)
            self.assertGreater(float(row["target_speed_kmh"]), 0.0)
            self.assertGreater(float(row["target_travel_time_s"]), 0.0)
            self.assertIn("low_speed_weight", row)

    def test_queue_classification_distinguishes_physical_queue(self):
        rows = [
            {"simulated_speed_kmh": 12, "max_density": 25, "max_occupancy": 0.05},
            {"simulated_speed_kmh": 15, "max_density": 10, "max_occupancy": 13.0},
            {"simulated_speed_kmh": 40, "max_density": 1, "max_occupancy": 0.0},
        ]
        result = self.pipeline.queue_audit_from_edges(rows)
        self.assertEqual(result["classification"], "physical_queue_congestion")
        sparse = [
            {"simulated_speed_kmh": 12, "max_density": 2, "max_occupancy": 0.01},
            {"simulated_speed_kmh": 15, "max_density": 3, "max_occupancy": 0.02},
        ]
        self.assertEqual(self.pipeline.queue_audit_from_edges(sparse)["classification"], "speed_only_delay")

    def test_selection_prefers_stable_candidate_over_teleport_candidate(self):
        payload = json.loads(SELECTION.read_text(encoding="utf-8"))
        selected = payload["selected"]
        self.assertEqual(selected["sumo_exit_code"], 0)
        self.assertTrue(selected["emergency_arrived"])
        self.assertIn(selected["candidate"], {row["candidate"] for row in payload["candidates"]})
        self.assertEqual(payload["manifest_selected_candidate"], "B04_j_balanced_recall")
        self.assertIn(payload["manifest_selection_policy"], {
            "updated_to_pass_or_warn",
            "retained_previous_because_all_candidates_failed",
        })

    def test_review_html_exists(self):
        text = REVIEW_HTML.read_text(encoding="utf-8")
        self.assertIn("Compact V9 B04", text)
        self.assertIn("toegye_ro_mainstream_segments_english.csv", text)
        self.assertIn("B04_j_balanced_recall", text)
        self.assertIn("진단상 최선 후보", text)

    def test_third_calibration_candidates_exist(self):
        self.assertIn("B04_k_city_behavior", self.pipeline.CANDIDATES)
        self.assertIn("B04_l_volume_calibrated", self.pipeline.CANDIDATES)
        self.assertIn("B04_m_hybrid_recall", self.pipeline.CANDIDATES)
        attrs = self.pipeline.vehicle_type_attrs(self.pipeline.CANDIDATES["B04_k_city_behavior"])
        self.assertLess(float(attrs["speedFactor"]), 1.0)
        self.assertGreater(float(attrs["tau"]), 1.0)

    def test_fourth_calibration_candidates_use_lightweight_measurement(self):
        for name in [
            "B04_n_speed50_sanity",
            "B04_o_speedfactor_only",
            "B04_p_k_light",
            "B04_q_midcorridor_flow",
            "B04_r_exit_relief",
            "B04_s_light_combined",
            "B04_u_speedfactor_exit_relief",
            "B04_v_queue_overlap_tuned",
            "B04_w_od_coverage_repair",
            "B04_x_od_queue_tuned",
            "B04_y_temporal_compression",
            "B04_z_signal_queue_pulse",
            "B04_aa_balanced_growth",
            "B04_ab_queue_pressure",
        ]:
            self.assertIn(name, self.pipeline.CANDIDATES)
            self.assertEqual(self.pipeline.CANDIDATES[name].get("net_profile"), "speed50_sanity")
        self.assertEqual(self.pipeline.CANDIDATES["B04_u_speedfactor_exit_relief"]["speed_factor"], 0.88)
        self.assertGreater(self.pipeline.CANDIDATES["B04_v_queue_overlap_tuned"]["midcorridor_share"], 0)
        self.assertGreater(self.pipeline.CANDIDATES["B04_w_od_coverage_repair"]["od_repair_share"], 0)
        self.assertGreater(self.pipeline.CANDIDATES["B04_x_od_queue_tuned"]["od_queue_tuned_share"], 0)
        self.assertEqual(self.pipeline.CANDIDATES["B04_y_temporal_compression"]["target_pulse_mode"], "compressed")
        self.assertEqual(self.pipeline.CANDIDATES["B04_z_signal_queue_pulse"]["target_pulse_mode"], "two_burst")
        self.assertGreater(self.pipeline.CANDIDATES["B04_aa_balanced_growth"]["use_balanced_main_through"], 0)
        self.assertGreater(self.pipeline.CANDIDATES["B04_ab_queue_pressure"]["through_scale_downbound"], 0.30)

    def test_candidate_subset_parser(self):
        self.assertEqual(
            self.pipeline.resolve_candidate_names("B04_u_speedfactor_exit_relief,B04_v_queue_overlap_tuned"),
            ["B04_u_speedfactor_exit_relief", "B04_v_queue_overlap_tuned"],
        )
        with self.assertRaises(Exception):
            self.pipeline.resolve_candidate_names("B04_missing")

    def test_write_sumo_config_does_not_enable_fcd_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.pipeline.write_sumo_config(
                "B04_n_speed50_sanity",
                self.pipeline.B04_DEMAND_DIR / "background_routes_compact_v9_B04_n_speed50_sanity.rou.xml",
                Path(tmp),
            )
            cfg_text = paths["sumocfg"].read_text(encoding="utf-8")
            add_text = paths["additional"].read_text(encoding="utf-8")
        self.assertIn("edgeData", add_text)
        self.assertIn("laneData", add_text)
        self.assertNotIn("fcd-output", cfg_text)
        self.assertNotIn("detectPersons", add_text)

    def test_lightweight_speed_sanity_classification(self):
        grouped = self.pipeline.mapping_by_segment_direction()
        first_edge = next(iter(next(iter(grouped.values()))))
        row = self.pipeline.segment_speed_rows(
            {first_edge: [{"speed": 65 / 3.6, "sampledSeconds": 1, "entered": 1, "left": 1, "departed": 0, "arrived": 0, "traveltime": 1, "density": 0, "occupancy": 0}]},
            {},
        )
        self.assertIn("speed_sanity_fail", [item["class"] for item in row])

    def test_free_flow_od_classification(self):
        self.assertEqual(self.pipeline.classify_free_flow_od(0, 10, 10, 5, 0, 3), "od_missing")
        self.assertEqual(self.pipeline.classify_free_flow_od(10, 10, 10, 5, 0, 3), "od_undercovered")
        self.assertEqual(self.pipeline.classify_free_flow_od(60, 10, 30, 14, 0, 3), "queue_not_forming")
        self.assertEqual(self.pipeline.classify_free_flow_od(60, 0, 30, 14, 0, 3), "measurement_warn")

    def test_queue_audit_classifies_new_queue_states(self):
        self.assertEqual(
            self.pipeline.classify_b04_queue_state({
                "simulated_speed_kmh": 12,
                "runtime_density_max": 30,
                "runtime_occupancy_max": 15,
                "runtime_waiting_or_timeloss_max": 80,
                "low_speed_interval_count": 4,
            }),
            "physical_queue",
        )
        self.assertEqual(
            self.pipeline.classify_b04_queue_state({
                "simulated_speed_kmh": 38,
                "runtime_density_max": 35,
                "runtime_occupancy_max": 16,
                "runtime_waiting_or_timeloss_max": 5,
            }),
            "fast_dense_flow",
        )
        self.assertEqual(
            self.pipeline.classify_b04_queue_state({
                "simulated_speed_kmh": 15,
                "runtime_density_max": 4,
                "runtime_occupancy_max": 2,
                "runtime_waiting_or_timeloss_max": 90,
                "low_speed_interval_count": 0,
            }),
            "signal_only_delay",
        )
        self.assertEqual(
            self.pipeline.classify_b04_queue_state({
                "simulated_speed_kmh": 45,
                "runtime_density_max": 5,
                "runtime_occupancy_max": 3,
                "runtime_waiting_or_timeloss_max": 0,
            }),
            "free",
        )
        self.assertEqual(
            self.pipeline.classify_b04_queue_state({"simulated_speed_kmh": 0}),
            "measurement_mismatch",
        )

    def test_queue_audit_generates_required_artifacts_without_running_sumo(self):
        original = self.pipeline.run_b0_candidate
        self.pipeline.run_b0_candidate = lambda _candidate: self.fail("queue audit must not run SUMO")
        try:
            summary = self.pipeline.build_b04_queue_audit()
        finally:
            self.pipeline.run_b0_candidate = original
        expected = {
            "queue_definition_audit_json",
            "queue_proxy_by_segment_csv",
            "stopline_queue_fill_ratio_csv",
            "queue_not_forming_diagnosis_csv",
            "segment_signal_presence_audit_csv",
            "queue_measurement_diagnostics_csv",
            "approach_storage_link_plan_csv",
            "case_b_queue_readiness_csv",
            "control_queue_threshold_proposal_json",
        }
        self.assertTrue(expected.issubset(summary["outputs"]))
        self.assertFalse(summary["fcd_enabled"])
        for rel_path in summary["outputs"].values():
            self.assertTrue((PROJECT_ROOT / rel_path).is_file(), rel_path)
            self.assertNotIn("b3_", Path(rel_path).name)
        self.assertNotIn("현실 queue length", (PROJECT_ROOT / summary["outputs"]["queue_definition_audit_json"]).read_text(encoding="utf-8"))
        self.assertIn("b4_queue_readiness", summary["queue_lenses"])

    def test_b04_approach_storage_plan_has_controllable_tls_rows(self):
        rows = self.pipeline.build_b4_approach_storage_link_plan()
        controllable = [row for row in rows if row["controllable"]]
        self.assertGreater(len(controllable), 0)
        for row in controllable:
            self.assertGreater(float(row["storage_length_m"]), 0.0)
            self.assertLessEqual(float(row["storage_length_m"]), 250.0)
            self.assertTrue(str(row["mapped_S_segment"]).startswith("S"))
            self.assertNotEqual(row["selected_green_phase"], "")

    def test_queue_proxy_keeps_s22_with_low_weight(self):
        speed_csv = PROJECT_ROOT / "results/metrics/compact_v9_B04/B04_j_balanced_recall/B04_segment_speed_recall.csv"
        with speed_csv.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        proxy_rows, summary = self.pipeline.build_b04_queue_proxy_rows(rows, "B04_j_balanced_recall")
        self.assertEqual(summary["segment_direction_count"], 44)
        s22_rows = [row for row in proxy_rows if row["segment_id"] == "S22"]
        self.assertGreater(len(s22_rows), 0)
        self.assertTrue(all(float(row["segment_weight"]) == 0.25 for row in s22_rows))

    def test_tau_fill_recommendation_uses_non_s22_controllable_percentiles(self):
        proposal = self.pipeline.case_b_tau_fill_proposal([
            {"controllable": True, "mapped_S_segment": "S1:upbound", "max_stopline_queue_fill_ratio": 0.10},
            {"controllable": True, "mapped_S_segment": "S22:upbound", "max_stopline_queue_fill_ratio": 1.00},
            {"controllable": False, "mapped_S_segment": "S3:upbound", "max_stopline_queue_fill_ratio": 1.00},
        ])
        self.assertEqual(proposal["percentile_sample_count"], 1)
        self.assertEqual(proposal["fill_ratio_p80"], 0.10)
        self.assertEqual(proposal["tau_fill_recommended"], 0.50)
        self.assertEqual(proposal["threshold_basis_ko"], "B04 B0 baseline 내부 stopline fill ratio 분포 기준")

    def test_free_flow_cause_classifier_covers_b4_review_causes(self):
        cases = {
            "physical_queue": ({"simulated_speed_kmh": 12, "runtime_density_max": 35, "runtime_occupancy_max": 15}, {}),
            "fast_dense_flow": ({"simulated_speed_kmh": 38, "runtime_density_max": 35, "runtime_occupancy_max": 15}, {}),
            "signal_only_delay": ({"simulated_speed_kmh": 20, "runtime_waiting_or_timeloss_max": 80}, {}),
            "measurement_mismatch": ({"simulated_speed_kmh": 0}, {}),
            "od_missing": ({"simulated_speed_kmh": 45, "target_queue_proxy": 1.0}, {"reason": "od_missing"}),
            "od_undercovered": ({"simulated_speed_kmh": 45, "target_queue_proxy": 1.0}, {"reason": "od_undercovered"}),
            "queue_not_forming": ({"simulated_speed_kmh": 45, "target_queue_proxy": 1.0}, {"reason": "queue_not_forming"}),
            "exit_too_easy": ({"segment_id": "S21", "simulated_speed_kmh": 45, "target_queue_proxy": 0.1}, {}),
            "signal_too_generous": ({"segment_id": "S10", "simulated_speed_kmh": 45, "target_queue_proxy": 0.1}, {}),
        }
        for expected, (row, od_row) in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(self.pipeline.classify_free_flow_cause(row, od_row), expected)

    def test_traffic_demand_review_generates_b4_named_outputs_without_running_sumo(self):
        original = self.pipeline.run_b0_candidate
        self.pipeline.run_b0_candidate = lambda _candidate: self.fail("traffic demand review must not run SUMO")
        try:
            summary = self.pipeline.build_b04_traffic_demand_review()
        finally:
            self.pipeline.run_b0_candidate = original
        self.assertEqual(summary["primary_candidate"], "B04_aa_balanced_growth")
        self.assertIn("B04_j_balanced_recall", summary["review_candidates"])
        self.assertIn("B04_z_signal_queue_pulse", summary["review_candidates"])
        self.assertIn("B04_ab_queue_pressure", summary["review_candidates"])
        self.assertTrue((PROJECT_ROOT / summary["outputs"]["traffic_demand_review_json"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["outputs"]["free_flow_cause_by_segment_csv"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["outputs"]["main_vs_offmain_demand_audit_csv"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["outputs"]["demand_growth_candidate_summary_csv"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["outputs"]["approach_storage_link_plan_csv"]).name.startswith("b4_"))
        self.assertIn("No field queue length exists", summary["queue_length_policy"])

    def test_review_summary_captures_wxyz_remaining_free_flow_counts(self):
        summary = self.pipeline.build_b04_traffic_demand_review()
        by_candidate = {row["candidate"]: row for row in summary["candidate_summaries"]}
        for name in ["B04_w_od_coverage_repair", "B04_x_od_queue_tuned", "B04_y_temporal_compression", "B04_z_signal_queue_pulse"]:
            self.assertIn(name, by_candidate)
            self.assertEqual(by_candidate[name]["od_missing_free_count"], 0)
        self.assertEqual(by_candidate["B04_x_od_queue_tuned"]["free_count"], 18)
        self.assertEqual(by_candidate["B04_x_od_queue_tuned"]["od_undercovered_free_count"], 10)
        self.assertEqual(by_candidate["B04_x_od_queue_tuned"]["queue_not_forming_free_count"], 8)

    def test_targeted_od_routes_include_screenline_edges(self):
        net = self.pipeline.read_sumo_net(self.pipeline.B04_NET)
        screenlines = self.pipeline.screenline_edges()
        templates, summary = self.pipeline.route_templates(net)
        self.assertGreater(summary["od_repair_template_count"], 0)
        for segment, direction in self.pipeline.OD_REPAIR_TARGETS:
            screenline = screenlines[(segment, direction)]
            routes = [
                edges for route_id, edges in templates.items()
                if route_id.startswith(f"od_repair_{direction}_{segment}_")
            ]
            self.assertGreaterEqual(len(routes), 1, f"missing OD repair route for {segment}:{direction}")
            self.assertTrue(all(screenline in edges for edges in routes))

    def test_balanced_main_through_templates_are_terminal_safe(self):
        net = self.pipeline.read_sumo_net(self.pipeline.B04_NET)
        terminal_edges = self.pipeline.terminal_source_edges()
        templates, summary = self.pipeline.route_templates(net)
        self.assertGreater(summary["balanced_main_through_template_count"], 0)
        balanced_routes = {
            route_id: edges
            for route_id, edges in templates.items()
            if route_id.startswith("mainline_through_") and "_balanced" in route_id
        }
        self.assertGreater(len(balanced_routes), 0)
        for route_id, edges in balanced_routes.items():
            with self.subTest(route_id=route_id):
                self.assertNotIn(edges[0], terminal_edges)
                self.assertNotIn(edges[-1], terminal_edges)

    def test_balanced_growth_demand_hits_main_through_target_without_sumo(self):
        original = self.pipeline.run_b0_candidate
        self.pipeline.run_b0_candidate = lambda _candidate: self.fail("demand build must not run SUMO")
        try:
            self.pipeline.build_demand(["B04_aa_balanced_growth"])
        finally:
            self.pipeline.run_b0_candidate = original
        rows = self.pipeline.build_main_vs_offmain_demand_rows(["B04_aa_balanced_growth"])
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(int(rows[0]["main_through_flow"]), 250)
        self.assertEqual(int(rows[0]["terminal_sink_flow"]), 0)

    def test_queue_measurement_diagnostic_flags_fast_dense_no_stopline_queue(self):
        speed_rows = [{
            "segment_id": "S13",
            "direction": "downbound",
            "simulated_speed_kmh": 45,
            "target_queue_proxy": 0.8,
            "runtime_density_max": 60,
            "runtime_occupancy_max": 30,
            "runtime_waiting_or_timeloss_max": 0,
        }]
        signal_rows = [{
            "mapped_S_segment": "S13:downbound",
            "signal_presence_status": "b4_controllable_tls",
            "b4_controllable_movement_count": 1,
            "net_tls_count": 1,
        }]
        fill_rows = [{"mapped_S_segment": "S13:downbound", "max_stopline_queue_fill_ratio": 0.05}]
        rows = self.pipeline.b4_queue_measurement_diagnostic_rows(speed_rows, signal_rows, fill_rows, "synthetic")
        self.assertEqual(rows[0]["diagnosis"], "signal_too_generous")
        self.assertGreater(float(rows[0]["fast_dense_no_queue_index"]), 0.0)

    def test_od_repair_vehicle_count_does_not_inflate_total_demand(self):
        baseline = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_v_queue_overlap_tuned.rou.summary.json"
        w_summary = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_w_od_coverage_repair.rou.summary.json"
        x_summary = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_x_od_queue_tuned.rou.summary.json"
        y_summary = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_y_temporal_compression.rou.summary.json"
        z_summary = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_z_signal_queue_pulse.rou.summary.json"
        if not (baseline.is_file() and w_summary.is_file() and x_summary.is_file()):
            self.skipTest("B04 W/X demand summaries are not generated yet")
        baseline_count = json.loads(baseline.read_text(encoding="utf-8"))["vehicle_count"]
        for path in [w_summary, x_summary, y_summary, z_summary]:
            if not path.is_file():
                continue
            count = json.loads(path.read_text(encoding="utf-8"))["vehicle_count"]
            self.assertLessEqual(count, int(baseline_count * 1.05))

    def test_target_depart_time_modes(self):
        compressed = self.pipeline.build_target_depart_time(
            0, 10, 1, self.pipeline.CANDIDATES["B04_y_temporal_compression"]
        )
        two_burst = self.pipeline.build_target_depart_time(
            0, 10, 1, self.pipeline.CANDIDATES["B04_z_signal_queue_pulse"]
        )
        self.assertGreaterEqual(compressed, 600.0)
        self.assertLessEqual(compressed, 840.0 + 3.0)
        self.assertGreaterEqual(two_burst, 620.0)
        self.assertLessEqual(two_burst, 900.0 + 3.0)


if __name__ == "__main__":
    unittest.main()
