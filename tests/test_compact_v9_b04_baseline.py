#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "09 Compact Corridor Baseline/b04_baseline_pipeline.py"
STAGE23_BUILDER = PROJECT_ROOT / "09 Compact Corridor Baseline/build_stage23_trigger_demand.py"
MANIFEST = PROJECT_ROOT / "configs/compact_v9_B04_b0_manifest.json"
TARGET_PROFILE = PROJECT_ROOT / "data_prepared/compact_v9/map/B04_target_profile.csv"
SELECTION = PROJECT_ROOT / "results/metrics/compact_v9_B04/selected/selection_summary.json"
REVIEW_HTML = PROJECT_ROOT / "results/html/compact_v9_B04_demand_validation_review.html"


def load_pipeline():
    return load_script("b04_baseline_pipeline_test", SCRIPT)


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CompactV9B04BaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = load_pipeline()
        cls.stage23_builder = load_script("stage23_builder_test", STAGE23_BUILDER)

    def test_teleport_summary_marks_emergency_teleport_separately(self):
        stderr = "\n".join([
            "Warning: Teleporting vehicle 'emergency_0'; waited too long (jam), lane='x_0', time=2400.00.",
            "Warning: Teleporting vehicle 'stage23_caseb_m09_trigger_001'; waited too long (jam), lane='y_0', time=2500.00.",
            "Warning: Teleporting vehicle 'B04_ad_variance_smoothed_00001'; waited too long (jam), lane='z_0', time=2600.00.",
        ])

        summary = self.pipeline.teleport_summary_from_stderr(stderr)

        self.assertTrue(summary["emergency_teleport"])
        self.assertEqual(summary["background_teleported"], 2)
        self.assertEqual(summary["stage23_teleported"], 1)
        self.assertEqual(summary["base_background_teleported"], 1)

    def test_firetruck_route_priority_promotes_uncontrolled_minor_connections_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            net_file = Path(tmp) / "net.xml"
            net_file.write_text(
                "<net>"
                "<connection from=\"a\" to=\"b\" fromLane=\"0\" toLane=\"0\" state=\"m\"/>"
                "<connection from=\"b\" to=\"c\" fromLane=\"0\" toLane=\"0\" state=\"o\"/>"
                "<connection from=\"a\" to=\"x\" fromLane=\"0\" toLane=\"0\" state=\"m\"/>"
                "<connection from=\"b\" to=\"c\" fromLane=\"1\" toLane=\"1\" state=\"m\" tl=\"tls0\"/>"
                "<connection from=\"c\" to=\"d\" fromLane=\"0\" toLane=\"0\" state=\"M\"/>"
                "</net>",
                encoding="utf-8",
            )
            original = self.pipeline.firetruck_route_edges
            self.pipeline.firetruck_route_edges = lambda: ["a", "b", "c"]
            try:
                summary = self.pipeline.promote_firetruck_route_priority_connections(net_file)
            finally:
                self.pipeline.firetruck_route_edges = original

            root = self.pipeline.ET.parse(net_file).getroot()
            states = [
                (conn.get("from"), conn.get("to"), conn.get("fromLane"), conn.get("tl", ""), conn.get("state"))
                for conn in root.findall("connection")
            ]

        self.assertEqual(summary["updated_connection_count"], 2)
        self.assertIn(("a", "b", "0", "", "M"), states)
        self.assertIn(("b", "c", "0", "", "M"), states)
        self.assertIn(("a", "x", "0", "", "m"), states)
        self.assertIn(("b", "c", "1", "tls0", "m"), states)

    def test_stage23_builder_records_explicit_trigger_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.rou.xml"
            output = Path(tmp) / "out.rou.xml"
            base.write_text(
                "<routes>"
                "<vType id=\"b04_passenger\"/>"
                "<route id=\"stage2_natural\" edges=\"x y a b c d\"/>"
                "<route id=\"stage3_direct_bad\" edges=\"c d e f\"/>"
                "<route id=\"stage3_natural\" edges=\"w x y c d e f\"/>"
                "<vehicle id=\"base0\" route=\"stage3_natural\" depart=\"1\"/>"
                "</routes>",
                encoding="utf-8",
            )
            stage1 = types.SimpleNamespace(
                route_edges=("ev0", "a", "b", "c", "d", "e", "f", "g", "h"),
                ev_depart_sec=100.0,
                departure=types.SimpleNamespace(mainline_target_edge="a"),
                movements=[types.SimpleNamespace(movement_id="B4_MOVEMENT_09", from_edge="c")],
            )

            summary = self.stage23_builder.build_stage23_trigger_demand(
                base_demand=base,
                output=output,
                stage2_count=3,
                stage2_headway_sec=1.5,
                stage3_count=2,
                stage3_headway_sec=4.0,
                stage3_route_length=4,
                stage2_queue_prebuild_sec=10.0,
                stage3_queue_prebuild_sec=20.0,
                stage1=stage1,
            )

        self.assertEqual(summary["stage2_vehicle_count"], 3)
        self.assertEqual(summary["stage2_headway_sec"], 1.5)
        self.assertEqual(summary["stage2_route_id"], "stage2_natural")
        self.assertEqual(summary["stage2_target_index"], 2)
        self.assertEqual(summary["vehicle_count"], 6)
        self.assertEqual(summary["stage23_added_count"], 5)
        self.assertEqual(summary["stage3_vehicle_count"], 2)
        self.assertEqual(summary["stage3_headway_sec"], 4.0)
        self.assertEqual(summary["stage3_route_length"], 4)
        self.assertEqual(summary["stage3_route_id"], "stage3_natural")
        self.assertEqual(summary["stage3_edges"], ["w", "x", "y", "c", "d", "e", "f"])
        self.assertEqual(summary["stage3_base_vehicle_count"], 1)
        self.assertGreaterEqual(summary["stage3_depart_window_sec"][0], 0.0)
        self.assertEqual(summary["s1forced_bottleneck_cap"]["final_bottleneck_vehicle_count"], 0)

    def test_stage23_route_candidates_exclude_direct_target_start(self):
        root = self.stage23_builder.ET.fromstring(
            "<routes>"
            "<route id=\"direct_bad\" edges=\"c d e f\"/>"
            "<route id=\"natural_ok\" edges=\"u v w c d e\"/>"
            "</routes>"
        )
        candidates = self.stage23_builder.natural_route_candidates(
            root,
            target_edge="c",
            min_upstream_edges=3,
            min_downstream_edges=2,
            excluded_start_edges={"c"},
        )

        self.assertEqual([candidate["route_id"] for candidate in candidates], ["natural_ok"])

    def test_s1forced_bottleneck_cap_keeps_stage23_and_samples_background(self):
        root = self.stage23_builder.ET.fromstring(
            "<routes>"
            "<route id=\"bn\" edges=\"a 347237859#0 b\"/>"
            "<route id=\"other\" edges=\"x y z\"/>"
            "<vehicle id=\"base_0\" route=\"bn\" depart=\"0\"/>"
            "<vehicle id=\"base_1\" route=\"bn\" depart=\"1\"/>"
            "<vehicle id=\"stage23_caseb_m09_trigger_000\" route=\"bn\" depart=\"2\"/>"
            "<vehicle id=\"other_0\" route=\"other\" depart=\"3\"/>"
            "</routes>"
        )

        summary = self.stage23_builder.cap_non_stage23_bottleneck_demand(
            root,
            bottleneck_edge="347237859#0",
            keep_share=0.0,
        )
        remaining_ids = [vehicle.get("id") for vehicle in root.findall("vehicle")]

        self.assertEqual(summary["total_bottleneck_vehicle_count"], 3)
        self.assertEqual(summary["stage23_bottleneck_vehicle_count"], 1)
        self.assertEqual(summary["removed_non_stage23_bottleneck_vehicle_count"], 2)
        self.assertEqual(remaining_ids, ["stage23_caseb_m09_trigger_000", "other_0"])
        final_counts = self.stage23_builder.bottleneck_vehicle_counts(root, bottleneck_edge="347237859#0")
        self.assertEqual(final_counts["final_bottleneck_vehicle_count"], 1)
        self.assertEqual(final_counts["final_stage23_bottleneck_vehicle_count"], 1)
        self.assertEqual(final_counts["final_non_stage23_bottleneck_vehicle_count"], 0)

    def test_s1forced_bottleneck_split_preserves_upstream_and_delays_downstream(self):
        root = self.stage23_builder.ET.fromstring(
            "<routes>"
            "<route id=\"bn\" edges=\"a b 347237859#0 c d\"/>"
            "<vehicle id=\"base_0\" route=\"bn\" depart=\"10\" departLane=\"best\"/>"
            "</routes>"
        )

        summary = self.stage23_builder.cap_non_stage23_bottleneck_demand(
            root,
            bottleneck_edge="347237859#0",
            keep_share=0.0,
            strategy="split",
            post_depart_delay_sec=900.0,
        )
        routes = {route.get("id"): route.get("edges") for route in root.findall("route")}
        vehicles = {vehicle.get("id"): vehicle.attrib for vehicle in root.findall("vehicle")}

        self.assertEqual(summary["split_pre_bottleneck_vehicle_count"], 1)
        self.assertEqual(summary["split_post_bottleneck_vehicle_count"], 1)
        self.assertEqual(routes["bn__s1pre_bn"], "a b")
        self.assertEqual(routes["bn__s1post_bn"], "c d")
        self.assertEqual(vehicles["base_0"]["route"], "bn__s1pre_bn")
        self.assertEqual(vehicles["base_0__post_bn"]["route"], "bn__s1post_bn")
        self.assertEqual(vehicles["base_0__post_bn"]["depart"], "910.00")

    def test_stage23_depart_time_is_computed_from_target_arrival(self):
        depart = self.stage23_builder.trigger_depart_base(
            net=None,
            ev_route_edges=("ev0", "target", "after"),
            ev_depart_sec=100.0,
            target_edge="target",
            trigger_route_edges=["a", "b", "target", "out"],
            queue_prebuild_sec=10.0,
        )

        self.assertAlmostEqual(depart["ev_target_arrival_sec"], 105.0)
        self.assertAlmostEqual(depart["trigger_time_to_target_sec"], 10.0)
        self.assertAlmostEqual(depart["depart_base_sec"], 85.0)

    def test_stage23_selector_uses_pass_observability_then_tie_breaks(self):
        records = [
            {
                "candidate": "fail_high",
                "status": "FAIL",
                "observability_score": 999,
                "background_teleported": 0,
                "speed_mae_kmh": 1,
                "free_count": 0,
                "params": {"stage3_count": 4},
            },
            {
                "candidate": "pass_low",
                "status": "PASS",
                "observability_score": 30,
                "background_teleported": 0,
                "speed_mae_kmh": 2,
                "free_count": 1,
                "params": {"stage3_count": 4},
            },
            {
                "candidate": "pass_high_more_teleport",
                "status": "PASS",
                "observability_score": 40,
                "background_teleported": 3,
                "speed_mae_kmh": 1,
                "free_count": 0,
                "params": {"stage3_count": 4},
            },
            {
                "candidate": "pass_high",
                "status": "PASS",
                "observability_score": 40,
                "background_teleported": 0,
                "speed_mae_kmh": 3,
                "free_count": 1,
                "params": {"stage3_count": 6},
            },
        ]

        selected = self.pipeline.select_stage23_calibration(records)

        self.assertEqual(selected["candidate"], "pass_high")

    def test_stage23_parameterized_build_uses_s1forced_global_net(self):
        captured: dict[str, object] = {}

        class FakeStage1Inputs:
            @staticmethod
            def load():
                return types.SimpleNamespace(route_edges=("a", "b"))

        class FakeStage23Builder:
            B4Stage1Inputs = FakeStage1Inputs

            @staticmethod
            def build_stage23_trigger_demand(**kwargs):
                captured.update(kwargs)
                return {"schema": "fake"}

        params = {
            "stage2_count": 12,
            "stage2_headway_sec": 2.0,
            "stage3_count": 4,
            "stage3_headway_sec": 6.0,
            "stage3_route_length": 6,
        }
        original_dir = self.pipeline.B04_DEMAND_DIR
        original_loader = self.pipeline.load_stage23_builder
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            base_route = tmp_dir / f"background_routes_compact_v9_{self.pipeline.B04_VARIANCE_SMOOTHED_CANDIDATE}.rou.xml"
            base_route.write_text("<routes/>", encoding="utf-8")
            self.pipeline.B04_DEMAND_DIR = tmp_dir
            self.pipeline.load_stage23_builder = lambda: FakeStage23Builder
            try:
                self.pipeline.build_stage23_demand_with_params("B04_ad_stage23_cal_test", params)
            finally:
                self.pipeline.B04_DEMAND_DIR = original_dir
                self.pipeline.load_stage23_builder = original_loader

        self.assertEqual(captured["net_file"], self.pipeline.B04_GLOBAL_REALITY_S1FORCED_NET)

    def test_manifest_uses_global_reality_net(self):
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(payload["baseline_name"], "B04")
        self.assertEqual(payload["mode"], "B0")
        self.assertEqual(payload["parameter_id"], "no_control")
        self.assertEqual(payload["active_net"], "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml")
        self.assertEqual(payload["background_route"], "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml")

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

    def test_runtime_queue_measurement_merge_keeps_fcd_speed_metrics(self):
        primary = {
            "segments": {
                ("S11", "downbound"): {
                    "fcd_stopped_mean_speed_kmh": 15.0,
                    "fcd_stopped_mean_sample_count": 120,
                    "runtime_queue_max_m": 0.0,
                    "runtime_density_max": 0.0,
                }
            },
            "summary": {"measurement_mode": "fcd_debug"},
        }
        supplemental = {
            "segments": {
                ("S11", "downbound"): {
                    "fcd_stopped_mean_speed_kmh": 4.0,
                    "runtime_queue_max_m": 38.0,
                    "runtime_density_max": 107.0,
                }
            }
        }

        merged = self.pipeline.merge_runtime_queue_measurements(primary, supplemental)
        row = merged["segments"][("S11", "downbound")]

        self.assertEqual(row["fcd_stopped_mean_speed_kmh"], 15.0)
        self.assertEqual(row["fcd_stopped_mean_sample_count"], 120)
        self.assertEqual(row["runtime_queue_max_m"], 38.0)
        self.assertEqual(row["runtime_density_max"], 107.0)
        self.assertEqual(merged["summary"]["runtime_queue_supplemental_source"], "edge_lane_data")

    def test_selection_prefers_stable_candidate_over_teleport_candidate(self):
        payload = json.loads(SELECTION.read_text(encoding="utf-8"))
        selected = payload["selected"]
        self.assertEqual(selected["sumo_exit_code"], 0)
        self.assertTrue(selected["emergency_arrived"])
        self.assertIn(selected["candidate"], {row["candidate"] for row in payload["candidates"]})
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["selected_candidate"], "B04_ad_stage23_trigger")
        self.assertEqual(payload["manifest_selected_candidate"], "B04_ad_stage23_trigger")
        self.assertIn(payload["manifest_selection_policy"], {
            "updated_to_pass_or_warn",
            "retained_previous_because_all_candidates_failed",
        })

    def test_review_html_exists(self):
        text = REVIEW_HTML.read_text(encoding="utf-8")
        self.assertIn("Compact V9 B04", text)
        self.assertIn("toegye_ro_mainstream_segments_english.csv", text)
        self.assertIn("B04_j_balanced_recall", text)
        self.assertIn("B04_ac_main_through_rebalanced", text)
        self.assertIn("B04_ad_variance_smoothed", text)
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
            "B04_ac_main_through_rebalanced",
            "B04_ad_variance_smoothed",
            "B04_ad_stage23_trigger",
        ]:
            self.assertIn(name, self.pipeline.CANDIDATES)
            if name != "B04_ad_stage23_trigger":
                self.assertEqual(self.pipeline.CANDIDATES[name].get("net_profile"), "speed50_sanity")
        self.assertEqual(self.pipeline.CANDIDATES["B04_u_speedfactor_exit_relief"]["speed_factor"], 0.88)
        self.assertGreater(self.pipeline.CANDIDATES["B04_v_queue_overlap_tuned"]["midcorridor_share"], 0)
        self.assertGreater(self.pipeline.CANDIDATES["B04_w_od_coverage_repair"]["od_repair_share"], 0)
        self.assertGreater(self.pipeline.CANDIDATES["B04_x_od_queue_tuned"]["od_queue_tuned_share"], 0)
        self.assertEqual(self.pipeline.CANDIDATES["B04_y_temporal_compression"]["target_pulse_mode"], "compressed")
        self.assertEqual(self.pipeline.CANDIDATES["B04_z_signal_queue_pulse"]["target_pulse_mode"], "two_burst")
        self.assertGreater(self.pipeline.CANDIDATES["B04_aa_balanced_growth"]["use_balanced_main_through"], 0)
        self.assertGreater(self.pipeline.CANDIDATES["B04_ab_queue_pressure"]["through_scale_downbound"], 0.30)
        self.assertGreater(self.pipeline.CANDIDATES["B04_ac_main_through_rebalanced"]["through_scale_upbound"], 0.30)
        self.assertLess(self.pipeline.CANDIDATES["B04_ad_variance_smoothed"]["pulse_share"], self.pipeline.CANDIDATES["B04_ac_main_through_rebalanced"]["pulse_share"])
        self.assertLess(self.pipeline.CANDIDATES["B04_ad_variance_smoothed"]["speed_dev"], self.pipeline.CANDIDATES["B04_ac_main_through_rebalanced"]["speed_dev"])

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
        low_support_row = self.pipeline.segment_speed_rows(
            {first_edge: [{"speed": 65 / 3.6, "sampledSeconds": 1, "entered": 1, "left": 1, "departed": 0, "arrived": 0, "traveltime": 1, "density": 0, "occupancy": 0}]},
            {},
        )
        self.assertIn("measurement_warn", [item["class"] for item in low_support_row])
        supported_row = self.pipeline.segment_speed_rows(
            {first_edge: [{"speed": 65 / 3.6, "sampledSeconds": 60, "entered": 60, "left": 60, "departed": 0, "arrived": 0, "traveltime": 1, "density": 0, "occupancy": 0}]},
            {},
        )
        self.assertIn("speed_sanity_fail", [item["class"] for item in supported_row])

    def test_stopped_edge_data_sample_is_included_in_speed_average(self):
        grouped = self.pipeline.mapping_by_segment_direction()
        first_edge = next(iter(next(iter(grouped.values()))))

        rows = self.pipeline.segment_speed_rows(
            {
                first_edge: [
                    {
                        "speed": 0.0,
                        "sampledSeconds": 60,
                        "entered": 10,
                        "left": 0,
                        "departed": 0,
                        "arrived": 0,
                        "traveltime": 60,
                        "density": 30,
                        "occupancy": 20,
                        "waitingTime": 60,
                        "timeLoss": 60,
                    },
                    {
                        "speed": 30 / 3.6,
                        "sampledSeconds": 60,
                        "entered": 10,
                        "left": 10,
                        "departed": 0,
                        "arrived": 0,
                        "traveltime": 10,
                        "density": 5,
                        "occupancy": 2,
                        "waitingTime": 0,
                        "timeLoss": 0,
                    },
                ]
            },
            {},
        )

        first_row = next(row for row in rows if row["edgeData_observed_count"] > 0)
        self.assertAlmostEqual(first_row["edgeData_speed_kmh"], 15.0, places=3)

    def test_segment_speed_range_audit_flags_out_of_range_s_segments(self):
        audit = self.pipeline.segment_speed_range_audit([
            {"segment_id": "S1", "direction": "upbound", "simulated_speed_kmh": 5.0, "class": "target_like"},
            {"segment_id": "S2", "direction": "upbound", "simulated_speed_kmh": 35.0, "class": "target_like"},
            {"segment_id": "S3", "direction": "downbound", "simulated_speed_kmh": 4.99, "class": "stop"},
            {"segment_id": "S4", "direction": "downbound", "simulated_speed_kmh": 35.01, "class": "free"},
        ])

        self.assertEqual(audit["status"], "FAIL")
        self.assertEqual(audit["fail_count"], 2)
        self.assertEqual(audit["low_speed_failures"][0]["segment_key"], "S3:downbound")
        self.assertEqual(audit["high_speed_failures"][0]["segment_key"], "S4:downbound")

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
        self.assertTrue(summary["fcd_enabled"])
        self.assertEqual(summary["measurement_mode"], "fcd_debug")
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
        self.assertEqual(summary["primary_candidate"], "B04_ad_stage23_trigger")
        self.assertEqual(summary["diagnostic_best_candidate"], "B04_ad_stage23_trigger")
        self.assertIn("B04_j_balanced_recall", summary["review_candidates"])
        self.assertIn("B04_z_signal_queue_pulse", summary["review_candidates"])
        self.assertIn("B04_ab_queue_pressure", summary["review_candidates"])
        self.assertIn("B04_ac_main_through_rebalanced", summary["review_candidates"])
        self.assertIn("B04_ad_variance_smoothed", summary["review_candidates"])
        self.assertIn("B04_ad_stage23_trigger", summary["review_candidates"])
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
                direction = "upbound" if "_upbound" in route_id else "downbound"
                self.assertEqual(edges[-1], templates[f"mainline_through_{direction}"][-1])

    def test_direction_rebalance_keeps_stage23_and_hits_share_window(self):
        root = self.stage23_builder.ET.fromstring(
            "<routes>"
            "<route id='mainline_through_upbound' edges='u0 u1 u2'/>"
            "<route id='mainline_through_upbound_balanced00' edges='u0 u1 u2'/>"
            "<route id='mainline_through_downbound' edges='d0 d1 d2'/>"
            "<route id='mainline_through_downbound_balanced00' edges='d0 d1 d2'/>"
            + "".join(f"<vehicle id='down_{idx}' route='mainline_through_downbound_balanced00' depart='{idx}'/>" for idx in range(80))
            + "".join(f"<vehicle id='up_{idx}' route='mainline_through_upbound_balanced00' depart='{idx}'/>" for idx in range(10))
            + "<vehicle id='stage23_caseb_m09_trigger_000' route='mainline_through_downbound_balanced00' depart='200'/>"
            "</routes>"
        )

        summary = self.stage23_builder.rebalance_direction_vehicle_counts(root)
        counts = self.stage23_builder.direction_vehicle_counts(root)
        stage23 = root.find("./vehicle[@id='stage23_caseb_m09_trigger_000']")

        self.assertGreater(summary["changed_vehicle_count"], 0)
        self.assertEqual(summary["after_status"]["status"], "PASS")
        self.assertGreaterEqual(counts["upbound"] / (counts["upbound"] + counts["downbound"]), 0.45)
        self.assertEqual(stage23.get("route"), "mainline_through_downbound_balanced00")

    def test_time_direction_rebalance_limits_each_departure_bin_to_40_60(self):
        root = self.stage23_builder.ET.fromstring(
            "<routes>"
            "<route id='mainline_through_upbound' edges='u0 u1 u2'/>"
            "<route id='mainline_through_upbound_balanced00' edges='u0 u1 u2'/>"
            "<route id='mainline_through_downbound' edges='d0 d1 d2'/>"
            "<route id='mainline_through_downbound_balanced00' edges='d0 d1 d2'/>"
            + "".join(f"<vehicle id='down_{idx}' route='mainline_through_downbound_balanced00' depart='{1320 + idx}'/>" for idx in range(18))
            + "".join(f"<vehicle id='up_{idx}' route='mainline_through_upbound_balanced00' depart='{1320 + idx}'/>" for idx in range(2))
            + "<vehicle id='stage23_caseb_m09_trigger_000' route='mainline_through_downbound_balanced00' depart='1321'/>"
            "</routes>"
        )

        summary = self.stage23_builder.rebalance_time_direction_vehicle_counts(root)
        status = self.stage23_builder.time_direction_balance_status(
            self.stage23_builder.direction_vehicle_counts_by_time_bin(root)
        )
        stage23 = root.find("./vehicle[@id='stage23_caseb_m09_trigger_000']")

        self.assertGreater(summary["changed_vehicle_count"], 0)
        self.assertEqual(status["status"], "PASS")
        self.assertEqual(status["fail_bin_count"], 0)
        self.assertEqual(stage23.get("route"), "mainline_through_downbound_balanced00")

    def test_time_direction_rebalance_treats_band_tune_vehicles_as_background(self):
        root = self.stage23_builder.ET.fromstring(
            "<routes>"
            "<route id='mainline_through_upbound' edges='u0 u1 u2'/>"
            "<route id='mainline_through_downbound' edges='d0 d1 d2'/>"
            + "".join(f"<vehicle id='stage23_band_tune_{idx:04d}' route='mainline_through_upbound' depart='156{idx}'/>" for idx in range(5))
            + "<vehicle id='stage23_caseb_m09_trigger_000' route='mainline_through_upbound' depart='1565'/>"
            "</routes>"
        )

        summary = self.stage23_builder.rebalance_time_direction_vehicle_counts(root)
        status = self.stage23_builder.time_direction_balance_status(
            self.stage23_builder.direction_vehicle_counts_by_time_bin(root)
        )
        protected = root.find("./vehicle[@id='stage23_caseb_m09_trigger_000']")

        self.assertEqual(summary["changed_vehicle_count"], 3)
        self.assertEqual(status["status"], "PASS")
        self.assertEqual(protected.get("route"), "mainline_through_upbound")

    def test_duplicate_mainroad_signal_candidates_keep_downstream_straight(self):
        candidates = [
            {
                "boundary_id": "S19_S20",
                "intersection_name": "Severance Building",
                "lat": 37.556492,
                "lon": 126.974231,
                "signal_type": self.pipeline.CSV_SIGNAL_TYPE_STRAIGHT,
                "segment_numbers": [19, 20],
            },
            {
                "boundary_id": "S21_S22",
                "intersection_name": "Seoul Square Front",
                "lat": 37.556152,
                "lon": 126.973187,
                "signal_type": self.pipeline.CSV_SIGNAL_TYPE_STRAIGHT,
                "segment_numbers": [21, 22],
            },
        ]

        kept, rows = self.pipeline.collapse_duplicate_mainroad_signal_candidates(candidates)

        self.assertEqual([candidate["boundary_id"] for candidate in kept], ["S21_S22"])
        self.assertEqual(rows[0]["removed_boundary_id"], "S19_S20")
        self.assertLessEqual(rows[0]["distance_m"], self.pipeline.CSV_SIGNAL_DUPLICATE_COLLAPSE_DISTANCE_M)

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

    def test_main_through_rebalanced_demand_keeps_total_and_rebalances_main(self):
        original = self.pipeline.run_b0_candidate
        candidates = ["B04_ac_main_through_rebalanced"]
        self.pipeline.run_b0_candidate = lambda _candidate: self.fail("demand build must not run SUMO")
        try:
            self.pipeline.build_demand(candidates)
        finally:
            self.pipeline.run_b0_candidate = original
        rows = self.pipeline.build_main_vs_offmain_demand_rows(candidates)
        for row in rows:
            with self.subTest(candidate=row["candidate"]):
                vehicle_count = int(row["vehicle_count"])
                main_through = int(row["main_through_flow"])
                self.assertGreaterEqual(vehicle_count, 1300)
                self.assertLessEqual(vehicle_count, 1500)
                self.assertGreaterEqual(main_through, 650)
                self.assertLessEqual(main_through, 800)
                self.assertGreaterEqual(main_through / vehicle_count, 0.50)
                self.assertLessEqual(float(row["off_main_background_share"]), 0.08)
                self.assertEqual(int(row["terminal_sink_flow"]), 0)
                self.assertLess(float(row["top_source_share"]), 0.25)

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
