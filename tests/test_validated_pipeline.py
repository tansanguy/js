from __future__ import annotations

import csv
import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = PROJECT_ROOT / "01-2 Validated/validated_pipeline.py"

spec = importlib.util.spec_from_file_location("validated_pipeline_under_test", PIPELINE_PATH)
assert spec and spec.loader
pipeline = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pipeline
spec.loader.exec_module(pipeline)


class ValidatedPipelineLaneOverrideTest(unittest.TestCase):
    def test_mainline_repair_target_uses_overlap_not_existing_lane_count(self) -> None:
        self.assertTrue(pipeline.is_repair_target(match_ratio=0.7, matched_length_m=90.0, segment_length_m=120.0))
        self.assertTrue(pipeline.is_repair_target(match_ratio=1.0, matched_length_m=12.0, segment_length_m=20.0))
        self.assertFalse(pipeline.is_repair_target(match_ratio=0.2, matched_length_m=90.0, segment_length_m=120.0))

    def test_lane_override_conflict_resolution_uses_dominant_overlap_and_ignores_non_targets(self) -> None:
        rows = [
            {
                "edge_id": "edge-a",
                "current_lanes": "2",
                "target_lanes": "3",
                "segment_id": "S1",
                "direction": "upbound",
                "repair_target": "True",
                "match_ratio": "0.20",
                "matched_length_m": "20",
            },
            {
                "edge_id": "edge-a",
                "current_lanes": "2",
                "target_lanes": "2",
                "segment_id": "S2",
                "direction": "downbound",
                "repair_target": "True",
                "match_ratio": "0.90",
                "matched_length_m": "90",
            },
            {
                "edge_id": "edge-b",
                "current_lanes": "2",
                "target_lanes": "5",
                "segment_id": "S3",
                "direction": "upbound",
                "repair_target": "False",
                "match_ratio": "1.00",
                "matched_length_m": "100",
            },
        ]

        overrides, summary = pipeline.build_lane_overrides(rows)

        self.assertEqual(summary["override_count"], 1)
        self.assertEqual(overrides[0]["edge_id"], "edge-a")
        self.assertEqual(overrides[0]["target_lanes"], 2)
        self.assertEqual(overrides[0]["lane_delta"], 0)
        self.assertEqual(overrides[0]["source_segment_ids"], "S1 S2")
        self.assertEqual(overrides[0]["source_directions"], "downbound upbound")
        self.assertEqual(overrides[0]["dominant_segment_ids"], "S2")

    def test_manual_lane_overrides_parse_and_take_precedence(self) -> None:
        rows = [
            {
                "edge_id": "edge-a",
                "current_lanes": "2",
                "target_lanes": "2",
                "segment_id": "S2",
                "direction": "upbound",
                "repair_target": "True",
                "match_ratio": "0.90",
                "matched_length_m": "90",
            }
        ]
        manual_rows = [
            {
                "edge_id": "edge-a",
                "target_lanes": "3",
                "source_segment_ids": "S9",
                "source_directions": "upbound",
                "repair_reason": "route_strict_lane_override",
            }
        ]

        overrides, summary = pipeline.build_lane_overrides(rows, manual_rows)

        self.assertEqual(summary["manual_override_count"], 1)
        self.assertEqual(overrides[0]["target_lanes"], 3)
        self.assertEqual(overrides[0]["lane_delta"], 1)
        self.assertEqual(overrides[0]["source_segment_ids"], "S9")
        self.assertEqual(overrides[0]["dominant_segment_ids"], "S9")
        self.assertEqual(overrides[0]["repair_reason"], "route_strict_lane_override")

    def test_manual_lane_override_csv_and_excluded_connectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "manual.csv"
            path.write_text(
                "edge_id,target_lanes,source_segment_ids,source_directions,repair_reason\n"
                "edge-a,3,S9,upbound,route_strict_lane_override\n",
                encoding="utf-8",
            )

            rows = pipeline.load_manual_lane_overrides(path)

        self.assertEqual(rows[0]["edge_id"], "edge-a")
        default_rows = pipeline.load_manual_lane_overrides(pipeline.DEFAULT_MANUAL_LANE_OVERRIDES_CSV)
        default_edge_ids = {row["edge_id"] for row in default_rows}
        self.assertNotIn("-273638834#4", default_edge_ids)
        self.assertNotIn("219696193#0", default_edge_ids)
        self.assertNotIn("219696193#1", default_edge_ids)

    def test_plain_edge_xml_num_lanes_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            edge_xml = tmp / "plain.edg.xml"
            edge_xml.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<edges>
  <edge id="edge-a" from="n1" to="n2" numLanes="2" speed="13.89"/>
  <edge id="edge-b" from="n2" to="n3" numLanes="1" speed="13.89"/>
</edges>
""",
                encoding="utf-8",
            )
            overrides_csv = tmp / "lane_overrides.csv"
            with overrides_csv.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["edge_id", "target_lanes"])
                writer.writeheader()
                writer.writerow({"edge_id": "edge-a", "target_lanes": "3"})
            output_xml = tmp / "repaired.edg.xml"

            summary = pipeline.rewrite_plain_edge_lanes(edge_xml, overrides_csv, output_xml)

            self.assertEqual(summary["changed_count"], 1)
            root = ET.parse(output_xml).getroot()
            lanes = {edge.get("id"): edge.get("numLanes") for edge in root.findall("edge")}
            self.assertEqual(lanes["edge-a"], "3")
            self.assertEqual(lanes["edge-b"], "1")


class ValidatedPipelineManifestTest(unittest.TestCase):
    def test_validated_manifest_sets_separate_net_demand_and_scales(self) -> None:
        payload = pipeline.validated_manifest_payload(
            pipeline.PROJECT_ROOT / "data_prepared/validated/net/test.net.xml",
            pipeline.PROJECT_ROOT / "data_prepared/validated/demand/background_routes_validated_warm0p4_sustain0p15.rou.xml",
            0.4,
            0.15,
            notes="unit test",
        )

        self.assertEqual(payload["schema"], "validated_experiment_manifest.v1")
        self.assertEqual(payload["active_net"], "data_prepared/validated/net/test.net.xml")
        self.assertEqual(
            payload["background_route"],
            "data_prepared/validated/demand/background_routes_validated_warm0p4_sustain0p15.rou.xml",
        )
        self.assertEqual(payload["background_demand_design"]["warmup_scale"], 0.4)
        self.assertEqual(payload["background_demand_design"]["sustain_scale"], 0.15)
        self.assertEqual(payload["final_background_required_substring"], "background_routes_validated_warm0p4_sustain0p15.rou.xml")

    def test_selection_score_prefers_pass_and_lower_focus_over_open_count(self) -> None:
        worse = {
            "lane_status": "PASS",
            "demand_status": "WARN",
            "speed_status": "WARN",
            "edge_speed_status": "FAIL",
            "speed_mae_kmh": "7.0",
            "s15_s22_over_open_edge_count": "50",
            "median_scaled_recall": "1.0",
        }
        better = dict(worse)
        better["edge_speed_status"] = "WARN"
        better["s15_s22_over_open_edge_count"] = "10"

        self.assertLess(pipeline.selection_score(better), pipeline.selection_score(worse))

    def test_needs_downstream_or_tls_for_poor_speed_or_weak_focus_reduction(self) -> None:
        self.assertTrue(
            pipeline.needs_downstream_or_tls_calibration(
                {
                    "speed_status": "FAIL",
                    "edge_speed_status": "WARN",
                    "s15_s22_over_open_edge_count": "10",
                }
            )
        )
        self.assertTrue(
            pipeline.needs_downstream_or_tls_calibration(
                {
                    "speed_status": "WARN",
                    "edge_speed_status": "WARN",
                    "s15_s22_over_open_edge_count": "49",
                }
            )
        )
        self.assertFalse(
            pipeline.needs_downstream_or_tls_calibration(
                {
                    "speed_status": "WARN",
                    "edge_speed_status": "WARN",
                    "s15_s22_over_open_edge_count": "30",
                    "background_teleported": "0",
                    "route_error_count": "0",
                }
            )
        )


class ValidatedPipelineReferenceDemandTest(unittest.TestCase):
    def test_reference_screenline_volume_parsing(self) -> None:
        rows = pipeline.reference_screenline_volumes(pipeline.DEFAULT_REFERENCE_CSV)

        self.assertEqual(len(rows), 22)
        self.assertEqual(rows[0]["segment_id"], "S1")
        self.assertEqual(rows[-1]["segment_id"], "S22")
        self.assertEqual(min(row["volume_vph"] for row in rows), 759.0)
        self.assertEqual(max(row["volume_vph"] for row in rows), 1128.0)

    def test_evenly_spaced_departures_uses_vph_and_duration(self) -> None:
        departures = pipeline.evenly_spaced_departures(1800.0, 7200.0, 0.5)

        self.assertEqual(len(departures), 3600)
        self.assertEqual(departures[0], 0.5)
        self.assertLess(departures[-1], 7200.5)

    def test_reference_screenline_demand_builds_connected_bidirectional_routes(self) -> None:
        if not pipeline.DEFAULT_REPAIRED_NET.is_file():
            self.skipTest("validated repaired net not available")
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_route = Path(tmp_dir) / "reference.rou.xml"

            flow_rows, summary = pipeline.build_reference_screenline_demand(
                pipeline.DEFAULT_REFERENCE_CSV,
                pipeline.DEFAULT_REPAIRED_NET,
                output_route,
                duration_sec=3600.0,
            )

            self.assertTrue(output_route.is_file())
            self.assertEqual(summary["base_through_vph"], 759.0)
            self.assertEqual(summary["leading_prefix_extra_vph"], 369.0)
            self.assertEqual(summary["vehicle_count"], 2256)
            self.assertEqual({row["direction"] for row in flow_rows}, {"upbound", "downbound"})
            root = ET.parse(output_route).getroot()
            self.assertEqual(len(root.findall("vehicle")), 2256)

    def test_reference_distributed_demand_uses_existing_od_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            reference = tmp / "reference.csv"
            with reference.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["segment_id", "peak_hour_volume_veh_per_h_reference"])
                writer.writeheader()
                for index in range(1, 23):
                    writer.writerow({"segment_id": f"S{index}", "peak_hour_volume_veh_per_h_reference": "60"})
            mapping = tmp / "mapping.csv"
            with mapping.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["edge_id", "segment_id", "direction", "repair_target"])
                writer.writeheader()
                writer.writerow({"edge_id": "a", "segment_id": "S1", "direction": "upbound", "repair_target": "True"})
                writer.writerow({"edge_id": "b", "segment_id": "S1", "direction": "downbound", "repair_target": "True"})
            base_route = tmp / "base.rou.xml"
            base_route.write_text(
                "<?xml version='1.0' encoding='UTF-8'?>\n"
                "<routes>\n"
                "  <vehicle id='up' depart='0'><route edges='x a y'/></vehicle>\n"
                "  <vehicle id='down' depart='0'><route edges='z b w'/></vehicle>\n"
                "</routes>\n",
                encoding="utf-8",
            )
            output_route = tmp / "distributed.rou.xml"

            segment_rows, summary = pipeline.build_reference_distributed_demand(
                reference,
                mapping,
                base_route,
                output_route,
                duration_sec=60.0,
                max_vehicles=10,
            )

            self.assertTrue(output_route.is_file())
            self.assertEqual(summary["vehicle_count"], 2)
            s1_rows = [row for row in segment_rows if row["segment_id"] == "S1"]
            self.assertEqual({row["direction"] for row in s1_rows}, {"upbound", "downbound"})
            self.assertEqual([row["generated_template_count"] for row in s1_rows], [1, 1])
            root = ET.parse(output_route).getroot()
            self.assertEqual(len(root.findall("vehicle")), 2)

    def test_reference_distributed_demand_v2_sets_depart_attributes(self) -> None:
        if not pipeline.DEFAULT_REPAIRED_NET.is_file():
            self.skipTest("validated repaired net not available")
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_route = Path(tmp_dir) / "distributed_v2.rou.xml"

            _segment_rows, summary = pipeline.build_reference_distributed_demand_v2(
                pipeline.DEFAULT_REFERENCE_CSV,
                pipeline.DEFAULT_MAPPING_CSV,
                pipeline.DEFAULT_BASE_DEMAND,
                pipeline.DEFAULT_REPAIRED_NET,
                output_route,
                duration_sec=60.0,
                max_vehicles=100,
                extension_steps=1,
            )

            self.assertTrue(output_route.is_file())
            self.assertGreater(summary["vehicle_count"], 0)
            root = ET.parse(output_route).getroot()
            vehicle = root.find("vehicle")
            self.assertIsNotNone(vehicle)
            assert vehicle is not None
            self.assertEqual(vehicle.get("departLane"), "best")
            self.assertEqual(vehicle.get("departPos"), "random_free")
            self.assertEqual(vehicle.get("departSpeed"), "max")


class ValidatedPipelineTlsBoundaryTest(unittest.TestCase):
    def test_tls_phase_rewrite_preserves_yellow_and_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            tll = tmp / "in.tll.xml"
            tll.write_text(
                "<?xml version='1.0' encoding='UTF-8'?>\n"
                "<tlLogics><tlLogic id='tls-a' type='static' programID='0' offset='0'>\n"
                "<phase duration='30' state='GGrr'/><phase duration='3' state='yyrr'/>\n"
                "<phase duration='30' state='rrGG'/><phase duration='3' state='rryy'/>\n"
                "</tlLogic></tlLogics>\n",
                encoding="utf-8",
            )
            audit = tmp / "audit.csv"
            audit.write_text("tls_id,green_phase_indices\n" "tls-a,0\n", encoding="utf-8")
            out = tmp / "out.tll.xml"

            summary = pipeline.rewrite_tllogic_for_candidate(tll, out, audit, 10.0, -10.0, 15.0)

            self.assertGreater(summary["changed_phase_count"], 0)
            logic = ET.parse(out).getroot().find("tlLogic")
            self.assertIsNotNone(logic)
            assert logic is not None
            phases = logic.findall("phase")
            self.assertEqual(sum(int(phase.get("duration", "0")) for phase in phases), 66)
            self.assertEqual(phases[1].get("duration"), "3")
            self.assertEqual(phases[3].get("duration"), "3")
            self.assertEqual(logic.get("offset"), "15")

    def test_plain_edge_speed_rewrite_only_caps_target_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            edge_xml = tmp / "in.edg.xml"
            edge_xml.write_text(
                "<?xml version='1.0' encoding='UTF-8'?>\n"
                "<edges><edge id='a' speed='13.89'/><edge id='b' speed='13.89'/></edges>\n",
                encoding="utf-8",
            )
            out = tmp / "out.edg.xml"

            summary = pipeline.rewrite_plain_edge_speeds(edge_xml, out, {"a"}, 8.33)

            self.assertEqual(summary["metered_edge_count"], 1)
            speeds = {edge.get("id"): edge.get("speed") for edge in ET.parse(out).getroot().findall("edge")}
            self.assertEqual(speeds["a"], "8.33")
            self.assertEqual(speeds["b"], "13.89")

    def test_tls_boundary_label_is_stable(self) -> None:
        self.assertEqual(
            pipeline.tls_boundary_label(10.0, -10.0, 15.0, "mild"),
            "tls_up10_dm10_off15_meter_mild",
        )


if __name__ == "__main__":
    unittest.main()
