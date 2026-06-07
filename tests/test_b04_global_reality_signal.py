from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = PROJECT_ROOT / "09 Compact Corridor Baseline/b04_global_reality_signal_pipeline.py"
TDATA_HELPER_PATH = PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_plausible_signal_pipeline.py"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("b04_global_reality_signal_test", PIPELINE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_tdata_helper():
    spec = importlib.util.spec_from_file_location("tdata_plausible_signal_test", TDATA_HELPER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class B04GlobalRealitySignalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = load_pipeline()
        cls.tdata = load_tdata_helper()

    def test_tls_phase_states_match_actual_link_indices(self):
        root = ET.fromstring(
            """
            <net>
              <connection from="a" to="b" tl="tls-a" linkIndex="0"/>
              <connection from="c" to="d" tl="tls-a" linkIndex="2"/>
              <connection from="e" to="f" tl="tls-b" linkIndex="1"/>
              <tlLogic id="tls-a" programID="0">
                <phase duration="10" state="GGGrrrr"/>
                <phase duration="3" state="yy"/>
              </tlLogic>
              <tlLogic id="tls-b" programID="0">
                <phase duration="10" state="G"/>
              </tlLogic>
            </net>
            """
        )

        stats = self.pipeline.normalize_tls_phase_state_lengths(root)

        tls_a = root.find("./tlLogic[@id='tls-a']")
        tls_b = root.find("./tlLogic[@id='tls-b']")
        assert tls_a is not None
        assert tls_b is not None
        self.assertEqual([phase.get("state") for phase in tls_a.findall("phase")], ["GGG", "yyr"])
        self.assertEqual([phase.get("state") for phase in tls_b.findall("phase")], ["Gr"])
        self.assertEqual(stats["normalized_phase_state_count"], 3)
        self.assertEqual(stats["normalized_tls_count"], 2)

    def test_api_record_selection_averages_new_and_old_samples(self):
        rows = [
            {
                "itstId": "111",
                "eqmnId": "EQ1",
                "dataId": "dup-1",
                "trsmUtcTime": "1000",
                "ntBssgRmdrCs": "100",
                "ntLtsgRmdrCs": "200",
            },
            {
                "itstId": "111",
                "eqmnId": "EQ1",
                "dataId": "dup-1",
                "trsmUtcTime": "2000",
                "ntBssgRmdrCs": "300",
                "ntLtsgRmdrCs": "400",
            },
            {
                "itstId": "111",
                "eqmnId": "EQ1",
                "dataId": "new-2",
                "trsmUtcTime": "3000",
                "ntBssgRmdrCs": "200",
                "ntLtsgRmdrCs": "300",
            },
        ]

        records = self.tdata.select_api_records(rows, 1)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.data_id, "avg:111:2")
        self.assertAlmostEqual(record.dominant_remaining_sec, 35.0)
        self.assertAlmostEqual(record.median_remaining_sec, 35.0)
        self.assertAlmostEqual(record.vehicle_field_count, 2.0)
        self.assertEqual(record.dominant_field, "ntLtsgRmdrCs")

    def test_direct_api_profile_keeps_red_time_for_stopped_average(self):
        timing = self.tdata.ApiSignalRecord(
            itst_id="111",
            eqmn_id="EQ1",
            data_id="d1",
            utc_ms=1000,
            reg_dt="",
            vehicle_values_sec=(20.0, 40.0),
            dominant_field="ntBssgRmdrCs",
            dominant_remaining_sec=40.0,
            median_remaining_sec=30.0,
            vehicle_field_count=4,
        )
        tls = self.pipeline.TlsPoint(
            tls_id="tls-a",
            x=1000.0,
            y=2000.0,
            lat=37.5,
            lon=127.0,
            coord_source="fixture",
            link_count=4,
            phase_count=4,
        )

        profile = self.pipeline.profile_from_timing(tls, "111", "fixture", timing, 20.0)

        self.assertLessEqual(profile.main_green_sec / profile.cycle_sec, 0.5)
        self.assertGreaterEqual(profile.side_green_sec, 12)

    def test_missing_green_link_repair_uses_existing_long_phase(self):
        root = ET.fromstring(
            """
            <net>
              <connection from="a" to="b" tl="tls-a" linkIndex="0"/>
              <connection from="c" to="d" tl="tls-a" linkIndex="1"/>
              <tlLogic id="tls-a" programID="0">
                <phase duration="30" state="Gr"/>
                <phase duration="3" state="yr"/>
                <phase duration="30" state="rr"/>
              </tlLogic>
            </net>
            """
        )

        stats = self.pipeline.repair_missing_green_links(root)

        phases = root.find("./tlLogic[@id='tls-a']").findall("phase")
        self.assertEqual(stats["missing_green_repaired_count"], 1)
        self.assertEqual(len(phases), 3)
        self.assertEqual(phases[0].get("state"), "Gg")
        self.assertEqual(phases[1].get("state"), "yy")

    def test_route_demand_counts_tls_controlled_edge_pairs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            net_path = Path(tmpdir) / "net.xml"
            route_path = Path(tmpdir) / "routes.rou.xml"
            net_path.write_text(
                """
                <net>
                  <connection from="a" to="b" tl="tls-a" linkIndex="0"/>
                  <connection from="b" to="c" tl="tls-b" linkIndex="0"/>
                  <connection from="x" to="y" linkIndex="0"/>
                </net>
                """,
                encoding="utf-8",
            )
            route_path.write_text(
                """
                <routes>
                  <route id="r1" edges="a b c"/>
                  <vehicle id="v1" route="r1"/>
                  <vehicle id="v2"><route edges="a b"/></vehicle>
                  <flow id="f1" route="r1" number="3"/>
                </routes>
                """,
                encoding="utf-8",
            )

            payload = self.pipeline.tls_route_demand_counts(net_path, route_path)

        self.assertEqual(payload["demand_source_status"], "loaded")
        self.assertEqual(payload["tls_counts"]["tls-a"], 5.0)
        self.assertEqual(payload["tls_counts"]["tls-b"], 4.0)

    def test_demand_adjustment_changes_non_virtual_profile(self):
        profile = self.tdata.SignalProfile(
            tls_id="tls-a",
            profile_role="global_average_fallback",
            source="fixture",
            source_itst_id="",
            source_eqmn_id="",
            source_tls_id="",
            movement_ids="",
            mapped_segments="",
            route_order_min=0.0,
            cycle_sec=100,
            main_green_sec=45,
            side_green_sec=49,
            yellow_sec=3,
            offset_sec=120,
            confidence=0.5,
            dominant_api_field="",
            dominant_remaining_sec=0.0,
            inference_reason="fixture",
        )

        adjusted = self.pipeline.demand_adjusted_profile(
            profile,
            {
                "demand_count": 100,
                "demand_pressure_factor": 1.0,
                "demand_cycle_delta_sec": 10,
                "demand_main_green_delta_sec": 5,
            },
        )

        self.assertEqual(adjusted.cycle_sec, 110)
        self.assertEqual(adjusted.main_green_sec, 50)
        self.assertEqual(adjusted.offset_sec, 10)
        self.assertIn("demand-proportional", adjusted.inference_reason)

    def test_duplicate_major_green_is_lowered_per_target_lane(self):
        root = ET.fromstring(
            """
            <net>
              <connection from="a" to="x" toLane="0" tl="tls-a" linkIndex="0"/>
              <connection from="b" to="x" toLane="0" tl="tls-a" linkIndex="1"/>
              <connection from="c" to="x" toLane="1" tl="tls-a" linkIndex="2"/>
              <tlLogic id="tls-a" programID="0">
                <phase duration="30" state="GGG"/>
              </tlLogic>
            </net>
            """
        )

        stats = self.pipeline.lower_duplicate_major_greens(root)

        phase = root.find("./tlLogic[@id='tls-a']/phase")
        self.assertEqual(phase.get("state"), "GgG")
        self.assertEqual(stats["duplicate_major_green_lowered_count"], 1)

    def test_b0_reference_route_matches_compact_route_edges(self):
        compact_edges = self.pipeline.route_edges_from_xml(self.pipeline.FIRETRUCK_ROUTE_XML)
        reference_edges = self.pipeline.route_edges_from_xml(self.pipeline.B0_ROUTE_REFERENCE_ROUTE_XML)

        self.assertEqual(len(compact_edges), 61)
        self.assertEqual(compact_edges, reference_edges)
        self.assertEqual(compact_edges[0], "420331801#1")
        self.assertEqual(compact_edges[1], "-174870621#8")
        self.assertEqual(compact_edges[-1], "619147738#1")

    def test_lane_recall_expands_collapsed_edge_to_target_count(self):
        edge = ET.fromstring(
            """
            <edge id="route_a" from="j0" to="j1">
              <lane id="route_a_0" index="0" speed="13.888889" length="10" shape="0,0 10,0"/>
            </edge>
            """
        )

        result = self.pipeline.set_regular_edge_lanes(edge, None, None, None, 3)

        lanes = edge.findall("lane")
        self.assertEqual(result["before_lane_count"], 1)
        self.assertEqual(result["after_lane_count"], 3)
        self.assertEqual([lane.get("id") for lane in lanes], ["route_a_0", "route_a_1", "route_a_2"])
        self.assertEqual([lane.get("index") for lane in lanes], ["0", "1", "2"])

    def test_route_geometry_stats_detects_extra_length(self):
        straight = self.pipeline.route_geometry_stats([(0.0, 0.0), (10.0, 0.0)])
        bent = self.pipeline.route_geometry_stats([(0.0, 0.0), (5.0, 5.0), (10.0, 0.0)])

        self.assertEqual(straight["extra_length_m"], 0.0)
        self.assertGreater(bent["extra_length_m"], 0.0)
        self.assertGreater(bent["max_lateral_deviation_m"], 0.0)


if __name__ == "__main__":
    unittest.main()
