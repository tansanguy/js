import csv
import importlib.util
import json
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = PROJECT_ROOT / "07 Expanded Validated/expanded_v7_pipeline.py"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("expanded_v7_pipeline_test", PIPELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ExpandedV7PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = load_pipeline()

    def test_bbox_expansion_is_about_100m_each_direction(self):
        old_meta = self.pipeline.read_json(self.pipeline.ANALYSIS_META)
        old_bbox = old_meta["bbox_wgs84"]
        new_bbox = self.pipeline.expanded_bbox_from_meta(self.pipeline.ANALYSIS_META)
        self.assertLess(new_bbox["min_lon"], old_bbox["min_lon"])
        self.assertLess(new_bbox["min_lat"], old_bbox["min_lat"])
        self.assertGreater(new_bbox["max_lon"], old_bbox["max_lon"])
        self.assertGreater(new_bbox["max_lat"], old_bbox["max_lat"])
        south = self.pipeline.haversine_distance_m(old_bbox["min_lat"], old_bbox["min_lon"], new_bbox["min_lat"], old_bbox["min_lon"])
        north = self.pipeline.haversine_distance_m(old_bbox["max_lat"], old_bbox["max_lon"], new_bbox["max_lat"], old_bbox["max_lon"])
        west = self.pipeline.haversine_distance_m(old_bbox["min_lat"], old_bbox["min_lon"], old_bbox["min_lat"], new_bbox["min_lon"])
        east = self.pipeline.haversine_distance_m(old_bbox["max_lat"], old_bbox["max_lon"], old_bbox["max_lat"], new_bbox["max_lon"])
        for distance in [south, north, west, east]:
            self.assertGreater(distance, 98.0)
            self.assertLess(distance, 102.0)

    def test_output_path_isolation(self):
        self.pipeline.ensure_isolated_output(self.pipeline.DATA_ROOT / "x/test.json")
        self.pipeline.ensure_isolated_output(self.pipeline.MANIFEST)
        with self.assertRaises(self.pipeline.ExpandedV7Error):
            self.pipeline.ensure_isolated_output(PROJECT_ROOT / "configs/not_allowed_expanded_v7.json")

    def test_firetruck_vtype_attrs(self):
        attrs = self.pipeline.firetruck_vtype_attrs()
        self.assertEqual(attrs["vClass"], "emergency")
        self.assertEqual(attrs["guiShape"], "emergency")
        self.assertEqual(attrs["color"], "1,0,0")
        self.assertEqual(attrs["length"], "8.0")
        self.assertEqual(attrs["width"], "2.5")
        self.assertAlmostEqual(float(attrs["maxSpeed"]) * 3.6, 70.0, places=3)
        self.assertLess(float(attrs["accel"]), 2.5)
        self.assertGreater(float(attrs["decel"]), 5.0)

    def test_conservative_firetruck_vtype_attrs_are_less_aggressive(self):
        aggressive = self.pipeline.firetruck_vtype_attrs()
        conservative = self.pipeline.conservative_firetruck_vtype_attrs()
        self.assertEqual(conservative["vClass"], "emergency")
        self.assertEqual(conservative["guiShape"], "emergency")
        self.assertLess(float(conservative["lcAssertive"]), float(aggressive["lcAssertive"]))
        self.assertGreater(float(conservative["lcCooperative"]), float(aggressive["lcCooperative"]))
        self.assertLess(float(conservative["lcStrategic"]), float(aggressive["lcStrategic"]))
        self.assertLess(float(conservative["lcSpeedGain"]), float(aggressive["lcSpeedGain"]))
        self.assertLess(float(conservative["speedFactor"]), float(aggressive["speedFactor"]))
        self.assertAlmostEqual(float(conservative["maxSpeed"]) * 3.6, 60.0, places=3)

    def test_acceptance_guard_requires_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing_acceptance.json"
            with self.assertRaises(self.pipeline.ExpandedV7Error):
                self.pipeline.read_route_acceptance(missing)

    def test_b0_run_requires_acceptance_json(self):
        original = self.pipeline.ROUTE_ACCEPTANCE_JSON
        with tempfile.TemporaryDirectory() as tmpdir:
            self.pipeline.ROUTE_ACCEPTANCE_JSON = Path(tmpdir) / "missing_acceptance.json"
            try:
                with self.assertRaises(self.pipeline.ExpandedV7Error):
                    self.pipeline.run_b0(accepted_routes=self.pipeline.ACCEPTED_ROUTES_CSV)
            finally:
                self.pipeline.ROUTE_ACCEPTANCE_JSON = original

    def test_manifest_schema_and_paths(self):
        payload = self.pipeline.manifest_payload()
        self.assertEqual(payload["schema"], "expanded_v7_b0_manifest.v1")
        self.assertEqual(payload["active_net"], "data_prepared/expanded_v7/net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_route_overopen_metered_release_fixed_lane_drop_fixed_plausibility_overopen.net.xml")
        self.assertEqual(payload["background_route"], "data_prepared/expanded_v7/demand/background_routes_expanded_v7_reference_main_sideflow.rou.xml")
        self.assertEqual(payload["background_demand_design"]["method"], "expanded_v7_reference_main_local_sideflow")
        self.assertEqual(payload["background_demand_design"]["profile"], "balanced_congestion_v8_stop_free_cleanup")
        self.assertAlmostEqual(float(payload["background_demand_design"]["upbound_through_share"]), 0.10, places=2)
        self.assertTrue(payload["background_demand_design"]["strict_bottleneck_route_guard"])
        self.assertTrue(payload["background_demand_design"]["local_accounting_guard_enabled"])
        self.assertTrue(payload["background_demand_design"]["distributed_boundary_enabled"])
        self.assertGreater(int(payload["background_demand_design"]["boundary_extension_applied_count"]), 0)
        self.assertTrue(payload["background_demand_design"]["terminal_sink_extension_v3_enabled"])
        self.assertGreater(int(payload["background_demand_design"]["terminal_sink_extension_v3_applied_count"]), 0)
        self.assertTrue(payload["background_demand_design"]["release_depart_gap_enabled"])
        self.assertTrue(payload["background_demand_design"]["free_segment_feeder_enabled"])
        self.assertTrue(payload["background_demand_design"]["plausibility_first"])
        self.assertTrue(payload["background_demand_design"]["generated_demand_recall_is_report_only"])
        self.assertTrue(payload["tls_fix"]["enabled"])
        self.assertEqual(payload["tls_fix"]["target_link_index"], 18)
        self.assertFalse(payload["release_speedcap"]["enabled"])
        self.assertFalse(payload["speedcap"]["enabled"])
        self.assertFalse(payload["overopen_metering"]["enabled"])
        self.assertTrue(payload["route_edge_overopen_metering"]["enabled"])
        self.assertEqual(float(payload["route_edge_overopen_metering"]["free_flow_threshold_kmh"]), 35.0)
        self.assertGreater(int(payload["route_edge_overopen_metering"]["changed_edge_count"]), 0)
        self.assertTrue(payload["release_junction_fixed"]["enabled"])
        self.assertGreater(int(payload["release_junction_fixed"]["changed_connection_count"]), 0)
        self.assertTrue(payload["lane_drop_fixed"]["enabled"])
        self.assertTrue(payload["plausibility_overopen_speedcap"]["enabled"])
        self.assertGreater(int(payload["lane_drop_fixed"]["changed_edge_count"]), 0)
        self.assertGreater(int(payload["lane_drop_fixed"]["changed_speed_count"]), 0)
        self.assertEqual(payload["route_set"], "custom_accepted")
        self.assertEqual(payload["emergency_depart_sec"], 600)
        self.assertEqual(payload["emergency_vtype_attrs"]["id"], "firetruck_emergency")

    def test_conservative_manifest_schema_and_route_xml(self):
        payload = self.pipeline.conservative_manifest_payload()
        self.assertEqual(payload["schema"], "expanded_v7_conservative_b0_manifest.v1")
        self.assertEqual(payload["active_net"], self.pipeline.rel(self.pipeline.MAKE_SENSE_FIXED_NET))
        self.assertTrue(payload["make_sense_fixed"]["enabled"])
        self.assertEqual(payload["make_sense_fixed"]["structural_defect_count"], 0)
        self.assertEqual(payload["make_sense_fixed"]["lane_drop_3_to_1_count"], 0)
        self.assertEqual(payload["make_sense_fixed"]["lane_drop_2_to_1_count"], 0)
        self.assertEqual(payload["make_sense_fixed"]["disconnected_pair_count"], 0)
        self.assertEqual(payload["emergency_behavior_profile"], "conservative_firetruck_b0")
        self.assertTrue(payload["disable_dynamic_emergency_insert"])
        self.assertEqual(payload["emergency_lane_guidance_mode"], "disabled")
        self.assertEqual(payload["emergency_vtype_attrs"]["id"], "firetruck_emergency_conservative_b0")
        route_xml = PROJECT_ROOT / payload["firetruck_route_xml"]
        self.assertTrue(route_xml.is_file(), route_xml)
        text = route_xml.read_text(encoding="utf-8")
        self.assertIn('id="firetruck_emergency_conservative_b0"', text)
        self.assertIn('departSpeed="0"', text)
        self.assertNotIn('insertionChecks="none"', text)

    def test_downstream_tls_green_split_fix(self):
        summary = self.pipeline.read_json(self.pipeline.TLS_FIX_SUMMARY_JSON)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["schema"], "expanded_v7_route_tls_green_split_fix.v2")
        self.assertGreaterEqual(summary["fixed_tls_count"], 2)
        self.assertGreaterEqual(summary["fixed_target_count"], 6)
        self.assertEqual(summary["tls_id"], self.pipeline.DOWNSTREAM_TLS_ID)
        self.assertEqual(summary["target_link_index"], 18)
        self.assertEqual(summary["before_cycle_sec"], 90)
        self.assertEqual(summary["after_cycle_sec"], 90)
        self.assertEqual(summary["before_link_green_sec"]["18"], 6)
        self.assertEqual(summary["after_link_green_sec"]["18"], 30)
        self.assertEqual(summary["before_link_yellow_sec"]["18"], summary["after_link_yellow_sec"]["18"])
        fixed_by_tls = {row["tls_id"]: row for row in summary["fixed_tls"]}
        upstream_tls = "joinedS_11346754524_11346754527_7335400049_cluster_11346754525_11346754526_2784736947_414685366_#2more"
        self.assertIn(upstream_tls, fixed_by_tls)
        self.assertEqual(fixed_by_tls[upstream_tls]["before_link_green_sec"]["8"], 12)
        self.assertEqual(fixed_by_tls[upstream_tls]["after_link_green_sec"]["8"], 18)
        self.assertEqual(fixed_by_tls[upstream_tls]["before_link_green_sec"]["25"], 12)
        self.assertEqual(fixed_by_tls[upstream_tls]["after_link_green_sec"]["25"], 18)
        self.assertEqual(summary["sumo_net_load"]["status"], "PASS")
        self.assertEqual(summary["route_connectivity"]["status"], "PASS")
        phases_before = summary["phase_duration_before"]
        phases_after = summary["phase_duration_after"]
        self.assertEqual(len(phases_before), len(phases_after))
        self.assertGreaterEqual(min(phase["duration"] for phase in phases_after), 1)
        self.assertTrue(self.pipeline.TLS_FIXED_NET.is_file())

    def test_downbound_metering_candidate_is_report_only(self):
        summary = self.pipeline.read_json(self.pipeline.DOWNBOUND_METERING_SUMMARY_JSON)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["schema"], "expanded_v7_downbound_metering_speedcap_net.v1")
        self.assertEqual(summary["direction_filter"], "downbound")
        self.assertGreater(summary["changed_edge_count"], 0)
        self.assertFalse(summary["selected_for_manifest"])
        self.assertEqual(summary["sumo_net_load"]["status"], "PASS")
        self.assertEqual(summary["route_connectivity"]["status"], "PASS")

    def test_overopen_metering_candidate_schema_when_available(self):
        if not self.pipeline.OVEROPEN_METERING_SUMMARY_JSON.is_file():
            self.skipTest("overopen metering candidate has not been generated")
        summary = self.pipeline.read_json(self.pipeline.OVEROPEN_METERING_SUMMARY_JSON)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["schema"], "expanded_v7_overopen_metering_speedcap_net.v1")
        self.assertGreater(summary["changed_edge_count"], 0)
        self.assertIn("b0_edge_speed_recall.csv", summary["source_edge_speed_csv"])
        self.assertEqual(summary["sumo_net_load"]["status"], "PASS")
        self.assertEqual(summary["route_connectivity"]["status"], "PASS")

    def test_route_edge_overopen_metering_candidate_schema_when_available(self):
        if not self.pipeline.ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON.is_file():
            self.skipTest("route-edge overopen metering candidate has not been generated")
        summary = self.pipeline.read_json(self.pipeline.ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["schema"], "expanded_v7_route_edge_overopen_metering_speedcap_net.v1")
        self.assertGreater(summary["changed_edge_count"], 0)
        self.assertEqual(float(summary["free_flow_threshold_kmh"]), 35.0)
        self.assertEqual(summary["sumo_net_load"]["status"], "PASS")
        self.assertEqual(summary["route_connectivity"]["status"], "PASS")

    def test_release_junction_fixed_candidate_schema_when_available(self):
        if not self.pipeline.RELEASE_JUNCTION_FIXED_SUMMARY_JSON.is_file():
            self.skipTest("release junction fixed candidate has not been generated")
        summary = self.pipeline.read_json(self.pipeline.RELEASE_JUNCTION_FIXED_SUMMARY_JSON)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["schema"], "expanded_v7_release_junction_fixed_net.v1")
        self.assertGreater(summary["changed_connection_count"], 0)
        self.assertEqual(summary["sumo_net_load"]["status"], "PASS")
        self.assertEqual(summary["route_connectivity"]["status"], "PASS")

    def test_route_edge_overopen_targets_include_firetruck_route_edges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "edge_speed.csv"
            self.pipeline.write_csv(path, [{
                "segment_id": "S2",
                "direction": "upbound",
                "edge_id": "-174870621#2",
                "edge_length_m": 64.0,
                "reference_segment_speed_kmh": 19.2,
                "simulated_edge_speed_kmh": 63.9,
                "speed_error_kmh": 44.7,
            }, {
                "segment_id": "S2",
                "direction": "upbound",
                "edge_id": "not_route_edge",
                "edge_length_m": 64.0,
                "reference_segment_speed_kmh": 19.2,
                "simulated_edge_speed_kmh": 63.9,
                "speed_error_kmh": 44.7,
            }], [
                "segment_id", "direction", "edge_id", "edge_length_m",
                "reference_segment_speed_kmh", "simulated_edge_speed_kmh", "speed_error_kmh",
            ])
            targets, _source = self.pipeline.overopen_speed_targets_from_validation(
                path,
                min_simulated_speed_kmh=35.0,
                min_speed_error_kmh=8.0,
                cap_margin_kmh=15.0,
                min_cap_kmh=30.0,
                max_cap_kmh=35.0,
                include_edge_ids={"-174870621#2"},
            )
        self.assertIn("-174870621#2", targets)
        self.assertNotIn("not_route_edge", targets)
        self.assertLessEqual(float(targets["-174870621#2"]["speed_cap_kmh"]), 35.0)

    def test_simple_edge_lane_targets_are_one_target_per_edge(self):
        target_csv = self.pipeline.EDGE_LANE_TARGETS_SIMPLE_CSV
        self.assertTrue(target_csv.is_file(), target_csv)
        rows = self.pipeline.read_csv(target_csv)
        edge_ids = [row["edge_id"] for row in rows]
        self.assertEqual(len(edge_ids), len(set(edge_ids)))
        self.assertGreater(len(rows), 150)
        for row in rows:
            self.assertGreaterEqual(int(float(row["target_lanes_simple"])), 1)
            self.assertNotEqual(row["target_lanes_simple"], "")
        smoothed = [row for row in rows if str(row.get("smoothing_applied")).lower() == "true"]
        self.assertGreater(len(smoothed), 0)

    def test_simple_edge_lane_recall_passes(self):
        summary = self.pipeline.read_json(self.pipeline.EDGE_LANE_RECALL_SIMPLE_CSV.with_suffix(".summary.json"))
        self.assertEqual(summary["status"], "PASS")
        self.assertGreaterEqual(float(summary["edge_level_lane_recall"]), 0.90)

    def test_demand_and_sideflow_assignment(self):
        route_xml = self.pipeline.DEMAND_XML
        self.assertTrue(route_xml.is_file(), route_xml)
        root = ET.parse(route_xml).getroot()
        vehicles = root.findall("vehicle")
        local = [vehicle for vehicle in vehicles if "_local_" in (vehicle.get("id") or "")]
        sideflow = [vehicle for vehicle in vehicles if (vehicle.get("id") or "").startswith("expanded_v7_sideflow_")]
        mapwide = [vehicle for vehicle in vehicles if (vehicle.get("id") or "").startswith("expanded_v7_mapwide_")]
        self.assertGreater(len(vehicles), 2000)
        self.assertGreater(len(local), 900)
        self.assertGreater(len(mapwide), 100)
        self.assertGreaterEqual(len(sideflow), 60)
        self.assertGreater(len(mapwide), len(sideflow))
        departs = [float(vehicle.get("depart", "0")) for vehicle in vehicles]
        self.assertEqual(departs, sorted(departs))

    def test_generated_demand_recall_and_source_caps(self):
        summary = self.pipeline.read_json(self.pipeline.DEMAND_XML.with_suffix(".summary.json"))
        self.assertEqual(summary["profile"], "balanced_congestion_v8_stop_free_cleanup")
        self.assertEqual(summary["timing_profile"], "csv_reality_sequential")
        self.assertAlmostEqual(float(summary["main_pass_ratio"]), 0.34, places=2)
        self.assertAlmostEqual(float(summary["diversion_share"]), 0.66, places=2)
        self.assertAlmostEqual(float(summary["bottleneck_local_validation_share"]), 0.0, places=3)
        self.assertAlmostEqual(float(summary["upbound_through_share"]), 0.10, places=2)
        self.assertAlmostEqual(float(summary["downbound_through_share"]), 0.32, places=2)
        self.assertTrue(summary["downstream_sink_guard_enabled"])
        self.assertTrue(summary["avoid_bottleneck_internal_local_source_sink"])
        self.assertTrue(summary["strict_bottleneck_route_guard"])
        self.assertTrue(summary["local_accounting_guard_enabled"])
        self.assertTrue(summary["distributed_boundary_enabled"])
        self.assertGreater(int(summary["boundary_extension_applied_count"]), 0)
        self.assertTrue(summary["terminal_sink_extension_v3_enabled"])
        self.assertGreater(int(summary["terminal_sink_extension_v3_applied_count"]), 0)
        self.assertTrue(summary["release_depart_gap_enabled"])
        self.assertTrue(summary["free_segment_feeder_enabled"])
        self.assertTrue(summary["plausibility_first"])
        self.assertTrue(summary["generated_demand_recall_is_report_only"])
        self.assertGreater(int(summary["accounted_only_flow_count"]), 0)
        self.assertTrue(summary["through_distribution"]["enabled"])
        self.assertGreaterEqual(int(summary["through_distribution"]["max_variants_per_through_flow"]), 24)
        self.assertGreaterEqual(int(summary["through_distribution"]["through_flow_rows"]), 12)
        self.assertGreater(int(summary["mapwide_background"]["vehicle_count"]), 600)
        self.assertGreaterEqual(int(summary["mapwide_background"]["route_template_count"]), 80)
        self.assertLessEqual(int(summary["mapwide_background"]["max_template_vehicle_count"]), 35)
        self.assertGreater(int(summary["mapwide_background"]["teleport_edge_blocklist_count"]), 0)
        self.assertGreaterEqual(float(summary["mean_generated_recall"]), 0.97)
        self.assertLessEqual(float(summary["mean_generated_recall"]), 1.05)
        self.assertGreaterEqual(float(summary["mean_mainline_generated_recall"]), 0.20)
        self.assertLessEqual(float(summary["mean_mainline_generated_recall"]), 0.35)
        self.assertGreaterEqual(float(summary["min_generated_recall"]), 0.97)
        self.assertLessEqual(float(summary["max_generated_recall"]), 1.05)
        flow_rows = self.pipeline.read_csv(self.pipeline.DEMAND_PROFILE_SUMMARY_CSV)
        self.assertTrue(all(row["timing_profile"] == "csv_reality_sequential" for row in flow_rows))
        self.assertGreater(len([row for row in flow_rows if row["flow_type"] == "free_segment_feeder"]), 0)
        up_sources = {
            row["start_edge"] for row in flow_rows
            if row["flow_id"].startswith("expanded_v7_ref_up_full_src")
        }
        self.assertGreaterEqual(len(up_sources), 3)
        local_s1_up = next(row for row in flow_rows if row["flow_id"] == "expanded_v7_ref_local_upbound_S01")
        local_s2_up = next(row for row in flow_rows if row["flow_id"] == "expanded_v7_ref_local_upbound_S02")
        self.assertEqual(float(local_s1_up["reference_offset_sec"]), 0.0)
        self.assertGreater(float(local_s2_up["reference_offset_sec"]), float(local_s1_up["reference_offset_sec"]))
        self.assertLess(float(local_s1_up["pulse_active_fraction"]), 1.0)
        source_rows = self.pipeline.read_csv(self.pipeline.SOURCE_ASSIGNMENT_SUMMARY_CSV)
        self.assertGreater(len(source_rows), 20)
        self.assertFalse(any(str(row["over_cap"]).lower() == "true" for row in source_rows))

    def test_bottleneck_aware_local_flows_avoid_internal_sources(self):
        flow_rows = self.pipeline.read_csv(self.pipeline.DEMAND_PROFILE_SUMMARY_CSV)
        bottleneck_local = [
            row for row in flow_rows
            if row["flow_id"].startswith("expanded_v7_ref_local_upbound_S")
            and 9 <= int(row["flow_id"].rsplit("S", 1)[1]) <= 17
        ]
        self.assertLessEqual(len(bottleneck_local), 9)
        for row in bottleneck_local:
            self.assertEqual(str(row["accounted_only"]).lower(), "true", row)
            self.assertEqual(int(float(row["vehicle_count"])), 0, row)
            self.assertIn("non_through_route_uses_upbound_bottleneck", row["route_guard_reason"], row)
            self.assertLessEqual(float(row["vph"]), 4.0)

    def test_balanced_congestion_bottleneck_load_is_reduced(self):
        root = ET.parse(self.pipeline.DEMAND_XML).getroot()
        watched_edges = {f"347237859#{index}" for index in range(6)} | {"218773869#6", "218773869#7", "781985787#0"}
        through_up_counts = {edge_id: 0 for edge_id in watched_edges}
        mapwide_counts = {edge_id: 0 for edge_id in watched_edges}
        sideflow_source_edges = set()
        for vehicle in root.findall("vehicle"):
            vehicle_id = vehicle.get("id", "")
            route = vehicle.find("route")
            edges = (route.get("edges", "").split() if route is not None else [])
            if vehicle_id.startswith("expanded_v7_sideflow_") and edges:
                sideflow_source_edges.add(edges[0])
            for edge_id in set(edges) & watched_edges:
                if vehicle_id.startswith("expanded_v7_ref_up_"):
                    through_up_counts[edge_id] += 1
                if vehicle_id.startswith("expanded_v7_mapwide_"):
                    mapwide_counts[edge_id] += 1
        self.assertLessEqual(max(through_up_counts.values()), 230)
        self.assertEqual(sum(mapwide_counts.values()), 0)
        self.assertFalse(any(self.pipeline.guarded_downstream_sink(edge_id) for edge_id in sideflow_source_edges))

    def test_balanced_congestion_profile_settings(self):
        low = self.pipeline.demand_profile_settings("balanced_congestion_v3_a")
        tuned = self.pipeline.demand_profile_settings("balanced_congestion_v3_tuned")
        up22 = self.pipeline.demand_profile_settings("balanced_congestion_v3_up22")
        base = self.pipeline.demand_profile_settings("balanced_congestion_v3")
        open_test = self.pipeline.demand_profile_settings("balanced_congestion_v3_c")
        downbound_mid = self.pipeline.demand_profile_settings("balanced_congestion_v3_down55")
        downbound_load = self.pipeline.demand_profile_settings("balanced_congestion_v3_down65")
        smooth_release = self.pipeline.demand_profile_settings("balanced_congestion_v4_smooth_release")
        distributed = self.pipeline.demand_profile_settings("balanced_congestion_v5_distributed_boundary")
        self.assertEqual(base["profile"], "balanced_congestion_v3")
        self.assertAlmostEqual(low["upbound_through_share"], 0.26)
        self.assertAlmostEqual(tuned["upbound_through_share"], 0.28)
        self.assertAlmostEqual(up22["upbound_through_share"], 0.22)
        self.assertAlmostEqual(up22["bottleneck_local_validation_share"], 0.005)
        self.assertAlmostEqual(base["upbound_through_share"], 0.30)
        self.assertAlmostEqual(open_test["upbound_through_share"], 0.34)
        self.assertAlmostEqual(downbound_mid["upbound_through_share"], 0.26)
        self.assertAlmostEqual(downbound_mid["downbound_through_share"], 0.55)
        self.assertAlmostEqual(downbound_load["upbound_through_share"], 0.26)
        self.assertAlmostEqual(downbound_load["downbound_through_share"], 0.65)
        self.assertAlmostEqual(base["bottleneck_local_validation_share"], 0.015)
        self.assertAlmostEqual(tuned["bottleneck_local_validation_share"], 0.01)
        self.assertAlmostEqual(open_test["bottleneck_local_validation_share"], 0.0)
        self.assertTrue(base["downstream_sink_guard_enabled"])
        self.assertTrue(base["strict_bottleneck_route_guard"])
        self.assertAlmostEqual(smooth_release["upbound_through_share"], 0.26)
        self.assertTrue(smooth_release["terminal_sink_extension_v2_enabled"])
        self.assertAlmostEqual(distributed["upbound_through_share"], 0.26)
        self.assertTrue(distributed["distributed_boundary_enabled"])
        self.assertEqual(distributed["mapwide_template_vehicle_cap"], 80)
        self.assertEqual(distributed["route_template_vehicle_cap"], 120)
        v6 = self.pipeline.demand_profile_settings("balanced_congestion_v6_boundary_balancer")
        self.assertAlmostEqual(v6["upbound_through_share"], 0.26)
        self.assertTrue(v6["terminal_sink_extension_v3_enabled"])
        self.assertTrue(v6["release_depart_gap_enabled"])
        self.assertTrue(v6["free_segment_feeder_enabled"])
        self.assertEqual(v6["mapwide_template_vehicle_cap"], 60)
        self.assertEqual(v6["route_template_vehicle_cap"], 80)
        v7 = self.pipeline.demand_profile_settings("balanced_congestion_v7_plausibility_first")
        self.assertAlmostEqual(v7["main_pass_ratio"], 0.42)
        self.assertAlmostEqual(v7["upbound_through_share"], 0.18)
        self.assertTrue(v7["plausibility_first"])
        self.assertTrue(v7["generated_demand_recall_is_report_only"])
        self.assertEqual(v7["mapwide_template_vehicle_cap"], 45)
        self.assertEqual(v7["route_template_vehicle_cap"], 60)

    def test_network_integrity_audit_schema(self):
        latest = PROJECT_ROOT / "results/metrics/expanded_v7_network_integrity_audit/latest.json"
        self.assertTrue(latest.is_file(), latest)
        payload = self.pipeline.read_json(latest)
        summary = self.pipeline.read_json(PROJECT_ROOT / payload["summary_json"])
        self.assertEqual(summary["schema"], "expanded_v7_network_integrity_audit.v1")
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["accepted_route_bad_pair_count"], 0)
        self.assertEqual(summary["invalid_demand_route_count"], 0)
        self.assertEqual(summary["forbidden_source_vehicle_count"], 0)
        self.assertEqual(summary["forbidden_sink_vehicle_count"], 0)
        self.assertGreaterEqual(summary["route_template_count"], 100)
        self.assertLessEqual(summary["max_template_vehicle_count"], 122)

    def test_road_integrity_audit_schema(self):
        latest = PROJECT_ROOT / "results/metrics/expanded_v7_road_integrity_audit/latest.json"
        self.assertTrue(latest.is_file(), latest)
        payload = self.pipeline.read_json(latest)
        summary = self.pipeline.read_json(PROJECT_ROOT / payload["summary_json"])
        self.assertEqual(summary["schema"], "expanded_v7_road_integrity_audit.v1")
        self.assertIn(summary["status"], {"PASS", "WARN"})
        self.assertGreater(summary["protected_edge_count"], 100)
        self.assertEqual(summary["protected_one_lane_edge_count"], 0)
        self.assertEqual(summary["lane_drop_3_to_1_count"], 0)
        self.assertTrue((PROJECT_ROOT / summary["edge_csv"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["connection_csv"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["html"]).is_file())

    def test_make_sense_classification_does_not_blanket_fix_side_streets(self):
        class FakeEdge:
            def __init__(self, lanes=1, length=3.0, speed=27.0):
                self._lanes = lanes
                self._length = length
                self._speed = speed / 3.6

            def getLaneNumber(self):
                return self._lanes

            def getLength(self):
                return self._length

            def getSpeed(self):
                return self._speed

            def getOutgoing(self):
                return {}

        class FakeNet:
            def getEdge(self, _edge_id):
                return FakeEdge()

        issues, recommendation, short_green = self.pipeline.classify_make_sense_edge(
            FakeNet(),
            "ordinary_side_street",
            [],
            4,
            0,
            0,
            {"observed_count": 1, "speed_kmh": 3.0},
            {},
        )
        self.assertEqual(issues, [])
        self.assertEqual(recommendation, "no_repair_needed")
        self.assertEqual(short_green, [])

    def test_make_sense_classification_flags_impassable_high_flow(self):
        class FakeTo:
            def getID(self):
                return "next_edge"

        class FakeLane:
            def __init__(self, index):
                self._index = index

            def getIndex(self):
                return self._index

        class FakeConn:
            def getTo(self):
                return FakeTo()

            def getFromLane(self):
                return FakeLane(0)

            def getToLane(self):
                return FakeLane(0)

            def getTLSID(self):
                return ""

            def getTLLinkIndex(self):
                return -1

            def getState(self):
                return "M"

        class FakeEdge:
            def getLaneNumber(self):
                return 3

            def getLength(self):
                return 70.0

            def getSpeed(self):
                return 50.0 / 3.6

            def getOutgoing(self):
                return {FakeTo(): [FakeConn()]}

        class FakeNet:
            def getEdge(self, _edge_id):
                return FakeEdge()

        issues, recommendation, _short_green = self.pipeline.classify_make_sense_edge(
            FakeNet(),
            "high_flow_edge",
            ["high_flow"],
            80,
            0,
            0,
            {"observed_count": 60, "speed_kmh": 2.0},
            {},
        )
        self.assertIn("effectively_impassable_pass_through", issues)
        self.assertEqual(recommendation, "inspect_connection_priority_tls_before_demand_changes")

    def test_make_sense_audit_schema_when_available(self):
        latest = PROJECT_ROOT / "results/metrics/expanded_v7_make_sense_audit/latest.json"
        if not latest.is_file():
            self.skipTest("make-sense audit has not been generated")
        payload = self.pipeline.read_json(latest)
        summary = self.pipeline.read_json(PROJECT_ROOT / payload["summary_json"])
        self.assertEqual(summary["schema"], "expanded_v7_make_sense_network_audit.v1")
        self.assertIn("toegye_ro_mainstream_segments_english.csv", summary["reference_csv_abs"])
        self.assertIn(summary["status"], {"PASS", "WARN"})
        self.assertIn("candidate_policy", summary)
        self.assertTrue((PROJECT_ROOT / summary["edge_csv"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["pair_csv"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["html"]).is_file())

    def test_lane_drop_fixed_candidate_schema(self):
        summary = self.pipeline.read_json(self.pipeline.LANE_DROP_FIXED_SUMMARY_JSON)
        self.assertEqual(summary["schema"], "expanded_v7_mainline_lane_drop_fixed_net.v1")
        self.assertEqual(summary["status"], "PASS")
        self.assertTrue(summary["selected_for_manifest"])
        self.assertGreater(summary["changed_edge_count"], 0)
        self.assertGreater(summary["changed_speed_count"], 0)
        self.assertEqual(summary["sumo_net_load"]["status"], "PASS")
        self.assertEqual(summary["route_connectivity"]["status"], "PASS")
        audit = summary["road_integrity_audit"]
        self.assertEqual(audit["protected_one_lane_edge_count"], 0)
        self.assertEqual(audit["lane_drop_3_to_1_count"], 0)
        self.assertTrue(self.pipeline.LANE_DROP_FIXED_NET.is_file())

    def test_35kmh_flow_classification(self):
        state, _reason = self.pipeline.classify_segment_flow_state(20.0, 4.9)
        self.assertEqual(state, "stop_flow")
        state, _reason = self.pipeline.classify_segment_flow_state(20.0, 35.0)
        self.assertNotEqual(state, "free_flow")
        state, _reason = self.pipeline.classify_segment_flow_state(20.0, 35.1)
        self.assertEqual(state, "free_flow")

    def test_congestion_sweep_candidate_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = self.pipeline.write_congestion_sweep_candidates({"speed_status": "FAIL", "demand_status": "FAIL"}, Path(tmpdir))
            payload = self.pipeline.read_json(Path(tmpdir) / "expanded_v7_congestion_sweep_candidates.json")
            rows = self.pipeline.read_csv(Path(tmpdir) / "expanded_v7_congestion_sweep_candidates.csv")
        self.assertEqual(payload["candidate_count"], 27)
        self.assertEqual(len(rows), 27)
        self.assertIn("bottleneck_aware_diversion", {row["demand_profile"] for row in rows})
        self.assertIn("balanced_diversion", {row["demand_profile"] for row in rows})
        self.assertIn("medium_downbound_metering", {row["boundary"] for row in rows})
        self.assertIn("mild_6to24_12to18", {row["tls_case"] for row in rows})
        self.assertTrue(output["csv"].endswith("expanded_v7_congestion_sweep_candidates.csv"))

    def test_bottleneck_diagnosis_schema(self):
        latest = PROJECT_ROOT / "results/metrics/expanded_v7_bottleneck_diagnosis/latest.json"
        self.assertTrue(latest.is_file(), latest)
        payload = self.pipeline.read_json(latest)
        summary = self.pipeline.read_json(PROJECT_ROOT / payload["summary_json"])
        self.assertEqual(summary["schema"], "expanded_v7_bottleneck_diagnosis.v1")
        self.assertTrue((PROJECT_ROOT / summary["edge_csv"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["route_contamination_csv"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["teleport_source_csv"]).is_file())
        self.assertTrue((PROJECT_ROOT / summary["short_edge_artifact_csv"]).is_file())
        self.assertGreaterEqual(int(summary["diagnosed_edge_count"]), 10)
        self.assertIn("total_source_internal_count", summary)
        self.assertIn("route_contamination", summary)
        self.assertIn("short_edge_artifacts", summary)

    def test_v3_route_contamination_guard_removes_non_through_bottleneck_routes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rows, _csv_path, summary = self.pipeline.write_route_contamination_report(
                self.pipeline.DEMAND_XML,
                Path(tmpdir),
            )
        self.assertEqual(summary["contaminated_non_through_count"], 0)
        self.assertEqual(rows, [])

    def test_short_edge_artifact_report_marks_connector_speeds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            net = self.pipeline.read_sumo_net(self.pipeline.active_b0_net())
            rows, _csv_path, summary = self.pipeline.write_short_edge_artifact_report(
                net,
                self.pipeline.bottleneck_diagnosis_edges(),
                Path(tmpdir),
            )
        by_edge = {row["edge_id"]: row for row in rows}
        self.assertIn("218915133#3", by_edge)
        self.assertEqual(by_edge["218915133#3"]["artifact_level"], "artifact_lt_5m")
        self.assertGreater(summary["short_edge_count"], 0)

    def test_balanced_congestion_sweep_summary_schema(self):
        path = self.pipeline.BALANCED_CONGESTION_SUMMARY
        self.assertTrue(path.is_file(), path)
        summary = self.pipeline.read_json(path)
        self.assertEqual(summary["schema"], "expanded_v7_balanced_congestion_sweep.v1")
        self.assertGreaterEqual(summary["candidate_count"], 3)
        self.assertIn(summary["selected_profile"], {
            "balanced_congestion_v3_a",
            "balanced_congestion_v3_tuned",
            "balanced_congestion_v3",
            "balanced_congestion_v3_c",
            "balanced_congestion_v3_a_aggressive_tls_release_speedcap",
            "balanced_congestion_v3_a_overopen_metering",
            "balanced_congestion_v3_a_route_edge35_metering",
            "balanced_congestion_v4_smooth_release",
            "balanced_congestion_v5_distributed_boundary",
            "balanced_congestion_v6_boundary_fanout_only",
            "balanced_congestion_v6_release_gap",
            "balanced_congestion_v6_free_feeder",
            "balanced_congestion_v6_boundary_balancer",
        })
        self.assertTrue((PROJECT_ROOT / summary["summary_csv"]).is_file())
        for row in summary["rows"]:
            self.assertIn("completion_rate", row)
            self.assertIn("release_edge_speed_kmh", row)
            self.assertIn("route_contaminated_non_through_count", row)
            self.assertIn("short_edge_artifact_count", row)
            self.assertIn("balanced_congestion_status", row)

    def test_v6_boundary_balancer_sweep_summary_schema_when_available(self):
        path = self.pipeline.V6_BOUNDARY_BALANCER_SUMMARY
        if not path.is_file():
            self.skipTest("v6 boundary balancer sweep has not been generated")
        summary = self.pipeline.read_json(path)
        self.assertEqual(summary["schema"], "expanded_v7_v6_boundary_balancer_sweep.v1")
        self.assertIn("toegye_ro_mainstream_segments_english.csv", summary["reference_csv_abs"])
        self.assertGreaterEqual(summary["candidate_count"], 1)
        self.assertIn(summary["selected_profile"], set(summary["profiles"]))
        self.assertTrue((PROJECT_ROOT / summary["summary_csv"]).is_file())
        for row in summary["rows"]:
            self.assertIn("grouped_stop_or_missing_count", row)
            self.assertIn("grouped_free_flow_count", row)
            self.assertIn("mean_generated_recall", row)
            self.assertIn("network_integrity_status", row)

    def test_sumo_net_route_and_demand_load(self):
        for route_file in [self.pipeline.ACCEPTED_ROUTE_XML, self.pipeline.DEMAND_XML]:
            completed = subprocess.run(
                [
                    "sumo",
                    "--net-file",
                    str(self.pipeline.REPAIRED_NET),
                    "--route-files",
                    str(route_file),
                    "--begin",
                    "0",
                    "--end",
                    "1",
                    "--no-step-log",
                    "true",
                    "--no-warnings",
                    "true",
                ],
                cwd=PROJECT_ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr[-1000:])

    def test_b0_results_and_fcd_presence(self):
        latest = self.pipeline.read_json(PROJECT_ROOT / "results/metrics/expanded_v7_b0/latest.json")
        with (PROJECT_ROOT / latest["results_csv"]).open() as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["mode"], "B0")
        self.assertEqual(row["parameter_id"], "no_control")
        self.assertEqual(row["sumo_exit_code"], "0")
        self.assertEqual(row["route_error_count"], "0")
        self.assertIn(row["emergency_teleport"], {"False", "True"})
        if row["final_status"] == "PASS":
            self.assertEqual(row["emergency_teleport"], "False")
        run_dir = PROJECT_ROOT / row["run_dir"]
        for filename in ["fcd.xml", "edgeData.xml", "tripinfo.xml"]:
            path = run_dir / filename
            self.assertTrue(path.is_file(), path)
            self.assertGreater(path.stat().st_size, 0)

    def test_validation_output_schema(self):
        latest = self.pipeline.read_json(PROJECT_ROOT / "results/metrics/expanded_v7_validation/latest.json")
        summary_path = PROJECT_ROOT / latest["summary_json"]
        summary = self.pipeline.read_json(summary_path)
        self.assertIn(summary["map_status"], {"PASS", "WARN", "FAIL"})
        self.assertIn(summary["lane_status"], {"PASS", "WARN", "FAIL"})
        self.assertIn(summary["demand_status"], {"PASS", "WARN", "FAIL"})
        self.assertIn(summary["speed_status"], {"PASS", "WARN", "FAIL"})
        for key in ["map_csv", "lane_csv", "demand_csv", "speed_csv", "edge_speed_csv", "summary_json"]:
            self.assertTrue((PROJECT_ROOT / summary["outputs"][key]).is_file())
        rec = Path(summary["outputs"]["summary_json"]).with_name("expanded_v7_report_only_recommendations.json")
        self.assertTrue((PROJECT_ROOT / rec).is_file())
        audit = self.pipeline.write_flow_plausibility_audit(summary, summary_path.parent)
        self.assertTrue((PROJECT_ROOT / audit["json"]).is_file())
        self.assertTrue((PROJECT_ROOT / audit["csv"]).is_file())
        audit_payload = self.pipeline.read_json(PROJECT_ROOT / audit["json"])
        self.assertEqual(audit_payload["schema"], "expanded_v7_flow_plausibility_audit.v1")
        self.assertIn("toegye_ro_mainstream_segments_english.csv", audit_payload["reference_csv_abs"])
        self.assertIn("may be aggregated", audit_payload["mapping_policy"])
        self.assertEqual(float(audit_payload["free_flow_threshold_kmh"]), 35.0)
        self.assertEqual(audit_payload["primary_gate"], "grouped_segment_speed")
        self.assertTrue((PROJECT_ROOT / audit_payload["grouped_csv"]).is_file())

    def test_validation_dashboard_visualizes_recall_dimensions(self):
        dashboard = PROJECT_ROOT / "results/html/expanded_v7_validation_review.html"
        self.assertTrue(dashboard.is_file(), dashboard)
        text = dashboard.read_text(encoding="utf-8")
        for needle in [
            "Expanded V7 Validation Dashboard",
            "Visual Map",
            "Bidirectional Congestion Recall",
            "Lane Recall Table",
            "Demand Recall Table",
            "Speed Recall Table",
            "Worst Edge Speed Recall",
            "Side-flow",
            "FCD seen after 600s",
            "<svg",
            "edge-slow",
            "edge-open",
            "side-line",
        ]:
            self.assertIn(needle, text)

    def test_directional_and_sideflow_recall_summary(self):
        summary, _path = self.pipeline.latest_validation_summary()
        data = self.pipeline.build_validation_dashboard_payload({"validation_summary": summary})
        self.assertIn(data["lane_certainty"], {"PARTIAL", "HIGH"})
        self.assertEqual(len(data["directional"]), 2)
        by_direction = {row["direction"]: row for row in data["directional"]}
        self.assertGreaterEqual(by_direction["upbound"]["over_congested_segments"], 1)
        self.assertGreaterEqual(by_direction["upbound"]["over_open_segments"], 1)
        self.assertGreater(by_direction["upbound"]["speed_mae_kmh"], 0)
        self.assertGreater(by_direction["downbound"]["over_open_segments"], by_direction["downbound"]["over_congested_segments"])
        self.assertGreater(data["sideflow"]["sideflow_vehicle_count"], 0)
        self.assertGreater(data["fcd_counts"]["emergency_seen"], 0)
        self.assertGreater(data["trip_counts"]["sideflow_arrived"], 0)


if __name__ == "__main__":
    unittest.main()
