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
    def test_lane_override_conflict_resolution_uses_max_target_and_ignores_non_targets(self) -> None:
        rows = [
            {
                "edge_id": "edge-a",
                "current_lanes": "2",
                "target_lanes": "3",
                "segment_id": "S1",
                "direction": "upbound",
                "repair_target": "True",
            },
            {
                "edge_id": "edge-a",
                "current_lanes": "2",
                "target_lanes": "4",
                "segment_id": "S2",
                "direction": "downbound",
                "repair_target": "True",
            },
            {
                "edge_id": "edge-b",
                "current_lanes": "2",
                "target_lanes": "5",
                "segment_id": "S3",
                "direction": "upbound",
                "repair_target": "False",
            },
        ]

        overrides, summary = pipeline.build_lane_overrides(rows)

        self.assertEqual(summary["override_count"], 1)
        self.assertEqual(overrides[0]["edge_id"], "edge-a")
        self.assertEqual(overrides[0]["target_lanes"], 4)
        self.assertEqual(overrides[0]["lane_delta"], 2)
        self.assertEqual(overrides[0]["source_segment_ids"], "S1 S2")
        self.assertEqual(overrides[0]["source_directions"], "downbound upbound")

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


if __name__ == "__main__":
    unittest.main()
