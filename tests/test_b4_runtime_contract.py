#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
B4_RUNTIME_SCRIPT = PROJECT_ROOT / "09 Compact Corridor Baseline/b4_runtime.py"
B4_RUNNER_SCRIPT = PROJECT_ROOT / "09 Compact Corridor Baseline/run_b0_b4_signal_pipeline.py"
B04_MANIFEST = PROJECT_ROOT / "configs/compact_v9_B04_b0_manifest.json"


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeSimulation:
    def __init__(self):
        self.time = 0.0
        self.departed: list[str] = []
        self.arrived: list[str] = []

    def getTime(self):
        return self.time

    def getDepartedIDList(self):
        return self.departed

    def getArrivedIDList(self):
        return self.arrived


class FakeTrafficLight:
    def __init__(self):
        self.phases: dict[str, int] = {}
        self.durations: dict[str, float] = {}
        self.actions: list[tuple[str, str, float | int]] = []

    def getPhase(self, tls_id):
        return self.phases.get(tls_id, 0)

    def setPhase(self, tls_id, phase):
        self.phases[tls_id] = phase
        self.actions.append(("setPhase", tls_id, phase))

    def setPhaseDuration(self, tls_id, duration):
        self.durations[tls_id] = duration
        self.actions.append(("setPhaseDuration", tls_id, duration))


class FakeVehicle:
    def __init__(self):
        self.vehicles: dict[str, dict[str, float | str | int]] = {}

    def getIDList(self):
        return list(self.vehicles)

    def getRoadID(self, vehicle_id):
        return self.vehicles[vehicle_id].get("edge", "")

    def getLaneID(self, vehicle_id):
        return self.vehicles[vehicle_id].get("lane", "")

    def getRouteIndex(self, vehicle_id):
        return self.vehicles[vehicle_id].get("route_index", -1)

    def getLanePosition(self, vehicle_id):
        return self.vehicles[vehicle_id].get("lane_position", 0.0)

    def getSpeed(self, vehicle_id):
        return self.vehicles[vehicle_id].get("speed", 0.0)

    def getWaitingTime(self, vehicle_id):
        return self.vehicles[vehicle_id].get("waiting", 0.0)

    def getTimeLoss(self, vehicle_id):
        return self.vehicles[vehicle_id].get("timeLoss", 0.0)


class FakeLane:
    def __init__(self, fake_vehicle: FakeVehicle):
        self.fake_vehicle = fake_vehicle
        self.lane_vehicles: dict[str, list[str]] = {}
        self.lane_speed: dict[str, float] = {}
        self.lane_occupancy: dict[str, float] = {}
        self.lane_length: dict[str, float] = {}

    def set_lane(self, lane_id: str, vehicle_ids: list[str], speed_mps: float, occupancy: float = 0.0, length: float = 100.0):
        self.lane_vehicles[lane_id] = vehicle_ids
        self.lane_speed[lane_id] = speed_mps
        self.lane_occupancy[lane_id] = occupancy
        self.lane_length[lane_id] = length

    def getLastStepVehicleIDs(self, lane_id):
        return self.lane_vehicles.get(lane_id, [])

    def getLastStepMeanSpeed(self, lane_id):
        return self.lane_speed.get(lane_id, 0.0)

    def getLastStepHaltingNumber(self, lane_id):
        return sum(1 for vehicle_id in self.lane_vehicles.get(lane_id, []) if self.fake_vehicle.getSpeed(vehicle_id) <= 0.1)

    def getLastStepOccupancy(self, lane_id):
        return self.lane_occupancy.get(lane_id, 0.0)

    def getLength(self, lane_id):
        return self.lane_length.get(lane_id, 100.0)


class FakeTraci:
    def __init__(self):
        self.simulation = FakeSimulation()
        self.vehicle = FakeVehicle()
        self.lane = FakeLane(self.vehicle)
        self.trafficlight = FakeTrafficLight()


class B4RuntimeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = load_script("b4_runtime_contract_module", B4_RUNTIME_SCRIPT)
        cls.runner = load_script("b4_runner_contract_module", B4_RUNNER_SCRIPT)
        cls.stage1 = cls.runtime.B4Stage1Inputs.load()

    def metric(
        self,
        movement,
        *,
        candidate=True,
        bottleneck=False,
        route_index=None,
        queue_m_proxy=None,
        local_fill_100m=None,
        approach_speed_kmh=None,
        trigger_reason=None,
    ):
        route_index = movement.route_order_index if route_index is None else route_index
        queue_m_proxy = 60.0 if queue_m_proxy is None and candidate else queue_m_proxy
        queue_m_proxy = 10.0 if queue_m_proxy is None else queue_m_proxy
        local_fill_100m = 0.60 if local_fill_100m is None and candidate else local_fill_100m
        local_fill_100m = 0.10 if local_fill_100m is None else local_fill_100m
        approach_speed_kmh = 10.0 if approach_speed_kmh is None and candidate else approach_speed_kmh
        approach_speed_kmh = 30.0 if approach_speed_kmh is None else approach_speed_kmh
        if trigger_reason is None:
            trigger_reason = "local_fill" if candidate else "not_triggered"
        return self.runtime.MovementRuntimeMetrics(
            movement=movement,
            queue_m_proxy=queue_m_proxy,
            corridor_queue_m_proxy=130.0 if bottleneck else 10.0,
            local_fill_80m=queue_m_proxy / 80.0,
            local_fill_100m=local_fill_100m,
            local_fill_120m=queue_m_proxy / 120.0,
            stopline_local_fill_100m=local_fill_100m,
            corridor_fill_250m=0.52 if bottleneck else 0.04,
            approach_speed_kmh=approach_speed_kmh,
            speed_observed=True,
            density=20.0,
            occupancy=15.0,
            waiting=20.0,
            time_loss=20.0,
            low_speed_count=8 if approach_speed_kmh <= self.stage1.thresholds.speed_trigger_kmh else 0,
            halting_count=0,
            fast_dense_flow=False,
            signal_only_delay=False,
            control_candidate=candidate,
            trigger_reason=trigger_reason,
            traffic_pressure=candidate,
            operational_queue=candidate,
            bottleneck_risk=bottleneck,
            control_mode="bottleneck_downstream_first" if bottleneck else "normal_preemptive",
            queue_confidence=self.runtime.QUEUE_PROXY_CONFIDENCE,
        )

    def test_stage1_load_contract_keeps_aa_primary_and_manifest_selected(self):
        manifest_before = B04_MANIFEST.read_text(encoding="utf-8")
        stage1 = self.runtime.B4Stage1Inputs.load()
        manifest_after = B04_MANIFEST.read_text(encoding="utf-8")
        self.assertEqual(manifest_before, manifest_after)
        self.assertEqual(stage1.primary_candidate, "B04_aa_balanced_growth")
        self.assertEqual(stage1.manifest_selected_candidate, "B04_aa_balanced_growth")
        self.assertEqual(stage1.manifest_selected_candidate_role, "primary_selected")
        self.assertEqual(stage1.max_active_movements, 3)
        self.assertEqual(stage1.departure.merge_control_tls, "COMPACT_V9_FIRE_STATION_ENTRY_TLS")
        self.assertEqual(stage1.departure.ev_release_control_status, "uncontrolled_by_merge_tls")
        self.assertEqual(stage1.stage2_merge_hold.measurement_source, "SUMO_B04_AA_B0_laneData_edgeData_proxy")
        self.assertAlmostEqual(stage1.stage2_merge_hold.L_merge_m, 50.0)
        self.assertAlmostEqual(stage1.stage2_merge_hold.C_merge_proxy_veh, 50.0 / 6.5, places=5)
        self.assertAlmostEqual(stage1.stage2_merge_hold.n_need_proxy_veh, 2.0)
        self.assertEqual({candidate.segment_id for candidate in stage1.case_b_candidates}, {"S7", "S10", "S11"})
        self.assertTrue(all(candidate.mapping_status == "mapped_route_span_proxy" for candidate in stage1.case_b_candidates))
        self.assertTrue(all(candidate.mapped for candidate in stage1.case_b_candidates))
        self.assertTrue(all(candidate.segment_lanes for candidate in stage1.case_b_candidates))
        self.assertTrue(stage1.queue_calibration_priors)
        self.assertTrue(all(prior.calibration_factor > 0.0 for prior in stage1.queue_calibration_priors.values()))

    def test_trigger_contract_uses_local_100m_or_speed(self):
        thresholds = self.runtime.B4Thresholds()
        self.assertTrue(
            self.runtime.evaluate_queue_levels(0.50, 0.0, 35.0, thresholds)["control_candidate"]
        )
        speed_result = self.runtime.evaluate_queue_levels(0.10, 0.0, 15.0, thresholds)
        self.assertTrue(speed_result["control_candidate"])
        self.assertEqual(speed_result["trigger_reason"], "low_speed")
        below = self.runtime.evaluate_queue_levels(0.49, 0.0, 15.1, thresholds)
        self.assertFalse(below["control_candidate"])
        self.assertEqual(below["trigger_reason"], "not_triggered")

    def test_fill_metric_contract_keeps_80_100_120_but_primary_is_100m(self):
        fill = self.runtime.compute_fill_metrics(60.0, 125.0, 250.0)
        self.assertAlmostEqual(fill["local_fill_80m"], 0.75)
        self.assertAlmostEqual(fill["local_fill_100m"], 0.60)
        self.assertAlmostEqual(fill["stopline_local_fill_100m"], 0.60)
        self.assertAlmostEqual(fill["local_fill_120m"], 0.50)
        self.assertAlmostEqual(fill["corridor_fill_250m"], 0.50)
        proposal = json.loads((PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1/b4_control_queue_threshold_proposal.json").read_text(encoding="utf-8"))
        self.assertEqual(proposal["primary_control_fill_metric"], "stopline_local_fill_100m")

    def test_ta_proxy_formula_contract(self):
        ta = self.runtime.compute_ta_proxy(
            ev_distance_m=139.0,
            queue_m_proxy=65.0,
            lane_count=2,
            previous_phase=0,
            target_phase=1,
        )
        self.assertAlmostEqual(ta.tE_sec, 10.0)
        self.assertAlmostEqual(ta.tS_sec, 5.0)
        self.assertAlmostEqual(ta.tQ_sec, 10.0)
        self.assertAlmostEqual(ta.TA_proxy_sec, -5.0)
        self.assertTrue(ta.ta_triggered)
        self.assertEqual(ta.queue_source, "runtime_proxy")
        self.assertEqual(ta.tS_source, "b0_phase_proxy")

    def test_ta_proxy_uses_b0_tq_when_runtime_queue_is_stale_or_empty(self):
        ta = self.runtime.compute_ta_proxy(
            ev_distance_m=139.0,
            queue_m_proxy=0.0,
            lane_count=2,
            previous_phase=1,
            target_phase=1,
            queue_confidence=self.runtime.QUEUE_STALE_CONFIDENCE,
            queue_method="lane_proxy",
            b0_tQ_hist_sec=7.0,
            b0_queue_veh=2.0,
        )
        self.assertAlmostEqual(ta.tE_sec, 10.0)
        self.assertAlmostEqual(ta.tS_sec, 0.0)
        self.assertAlmostEqual(ta.tQ_sec, 7.0)
        self.assertAlmostEqual(ta.TA_proxy_sec, 3.0)
        self.assertEqual(ta.queue_source, "b0_fallback")
        self.assertEqual(ta.tS_source, "current_phase_direct")

    def test_stage2_entry_hold_and_release_preserves_ev_uncontrolled_warn(self):
        traci = FakeTraci()
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        traci.simulation.time = self.stage1.ev_depart_sec - self.stage1.departure.dispatch_lead_time_sec
        early_events = controller.handle_stage2(traci.simulation.time, controller.ev_state())
        if self.stage1.stage2_merge_hold.runtime_control_uses_formula_directly:
            self.assertEqual(early_events, [])

        traci.simulation.time = self.stage1.ev_depart_sec - 4.0
        hold_events = controller.handle_stage2(traci.simulation.time, controller.ev_state())
        self.assertEqual(len(hold_events), 1)
        self.assertEqual(hold_events[0]["action_type"], "entry_hold")
        self.assertEqual(hold_events[0]["tls_id"], "COMPACT_V9_FIRE_STATION_ENTRY_TLS")
        self.assertEqual(hold_events[0]["target_phase"], 2)
        self.assertEqual(hold_events[0]["safety_status"], "ev_release_uncontrolled_warn")
        self.assertEqual(hold_events[0]["stage2_measurement_source"], "SUMO_B04_AA_B0_laneData_edgeData_proxy")
        self.assertEqual(float(hold_events[0]["L_merge_m"]), 50.0)
        self.assertIn("T_hold_proxy_sec", hold_events[0])
        self.assertIn("n_occ_runtime_veh", hold_events[0])
        self.assertEqual(hold_events[0]["runtime_or_b0_fallback"], "runtime")
        self.assertAlmostEqual(float(hold_events[0]["T_hold_proxy_sec"]), 4.992806, places=5)
        self.assertEqual(traci.trafficlight.phases["COMPACT_V9_FIRE_STATION_ENTRY_TLS"], 2)

        traci.vehicle.vehicles[self.stage1.ev_id] = {
            "edge": "-174870621#8",
            "lane": "-174870621#8_0",
            "route_index": 1,
            "lane_position": 1.0,
            "speed": 8.0,
        }
        traci.simulation.time += 10
        release_events = controller.handle_stage2(traci.simulation.time, controller.ev_state())
        self.assertEqual(len(release_events), 1)
        self.assertEqual(release_events[0]["action_type"], "entry_hold_release")
        self.assertEqual(release_events[0]["target_phase"], 0)
        self.assertEqual(release_events[0]["stage2_measurement_source"], "SUMO_B04_AA_B0_laneData_edgeData_proxy")
        self.assertEqual(traci.trafficlight.phases["COMPACT_V9_FIRE_STATION_ENTRY_TLS"], 0)

        traci.vehicle.vehicles.clear()
        traci.simulation.time += 500
        self.assertEqual(controller.handle_stage2(traci.simulation.time, controller.ev_state()), [])

    def test_stage3_ordering_caps_active_movements_and_keeps_nearest_ahead_first(self):
        all_candidate_metrics = [self.metric(movement) for movement in self.stage1.movements]
        selected = self.runtime.order_stage3_candidates(all_candidate_metrics, current_route_index=0, max_active=3)
        self.assertEqual(len(selected), 3)
        self.assertEqual([item.movement.route_order_index for item in selected], sorted(item.movement.route_order_index for item in selected))

        bottleneck_metrics = [
            self.metric(movement, bottleneck=(movement == self.stage1.movements[-1]))
            for movement in self.stage1.movements
        ]
        selected_bottleneck = self.runtime.order_stage3_candidates(bottleneck_metrics, current_route_index=0, max_active=3)
        self.assertEqual(len(selected_bottleneck), 3)
        self.assertEqual([item.movement.route_order_index for item in selected_bottleneck], sorted(item.movement.route_order_index for item in selected_bottleneck))

    def test_case_b_candidate_order_puts_bottleneck_before_upstream_when_runtime_tau_trips(self):
        upstream = self.stage1.movements[0]
        bottleneck = self.stage1.movements[1]
        stage1 = replace(
            self.stage1,
            case_b_candidates=(
                self.runtime.B4CaseBCandidate(
                    segment_id="S_TEST",
                    bottleneck_movement_id=bottleneck.movement_id,
                    upstream_movement_id=upstream.movement_id,
                    L_b0_m=100.0,
                    lane_drop_delta=1,
                    q_avg_B0=1.0,
                    q_max_B0=2.0,
                    tQ_hist_B0=4.0,
                    lambda_B0=100.0,
                    fill_B0=0.80,
                    speed_B0=10.0,
                    mapping_status="mapped",
                    tau_default=0.75,
                    case_b_prior_risk=True,
                ),
            ),
        )
        controller = self.runtime.B4RuntimeController(traci=FakeTraci(), stage1=stage1, run_id="contract")
        upstream_metric = replace(self.metric(upstream, queue_m_proxy=10.0), queue_confidence=self.runtime.QUEUE_PROXY_CONFIDENCE)
        bottleneck_metric = replace(self.metric(bottleneck, queue_m_proxy=80.0), queue_confidence=self.runtime.QUEUE_PROXY_CONFIDENCE)
        metrics_by_id = {
            upstream.movement_id: upstream_metric,
            bottleneck.movement_id: bottleneck_metric,
        }
        ordered = controller.order_case_b_candidates(
            [upstream_metric, bottleneck_metric],
            metrics_by_id,
            current_route_index=0,
            max_active=3,
        )
        self.assertEqual([metric.movement.movement_id for metric in ordered[:2]], [bottleneck.movement_id, upstream.movement_id])
        case_b = controller.case_b_evaluation(
            bottleneck_metric,
            metrics_by_id,
            {upstream.movement_id: 50.0, bottleneck.movement_id: 60.0},
            controller.metric_ta(bottleneck_metric, {bottleneck.movement_id: 60.0}, previous_phase=bottleneck.selected_red_phase),
        )
        self.assertEqual(case_b.case_b_source, "runtime_tau_movement")
        self.assertEqual(case_b.TA_case, "caseB_bottleneck")
        self.assertNotEqual(case_b.TA_upstream_sec, "")
        self.assertNotEqual(case_b.TA_bottleneck_sec, "")

    def test_case_b_segment_fill_triggers_before_movement_queue(self):
        upstream = self.stage1.movements[0]
        bottleneck = self.stage1.movements[1]
        candidate = self.runtime.B4CaseBCandidate(
            segment_id="S_TEST",
            bottleneck_movement_id=bottleneck.movement_id,
            upstream_movement_id=upstream.movement_id,
            L_b0_m=100.0,
            lane_drop_delta=1,
            q_avg_B0=1.0,
            q_max_B0=2.0,
            tQ_hist_B0=4.0,
            lambda_B0=100.0,
            fill_B0=0.10,
            speed_B0=30.0,
            mapping_status="mapped_route_span_proxy",
            tau_default=0.75,
            case_b_prior_risk=False,
            segment_lanes=("lane_a",),
            case_b_runtime_enabled=True,
        )
        stage1 = replace(self.stage1, case_b_candidates=(candidate,))
        controller = self.runtime.B4RuntimeController(traci=FakeTraci(), stage1=stage1, run_id="contract")
        upstream_metric = replace(self.metric(upstream, queue_m_proxy=0.0), queue_confidence=self.runtime.QUEUE_PROXY_CONFIDENCE)
        bottleneck_metric = replace(self.metric(bottleneck, queue_m_proxy=10.0), queue_confidence=self.runtime.QUEUE_PROXY_CONFIDENCE)
        metrics_by_id = {upstream.movement_id: upstream_metric, bottleneck.movement_id: bottleneck_metric}
        segment_metrics = {
            "S_TEST": self.runtime.CaseBSegmentRuntimeMetrics(
                segment_id="S_TEST",
                queue_m_proxy=80.0,
                fill=0.80,
                queue_confidence=self.runtime.QUEUE_PROXY_CONFIDENCE,
                observed_lane_count=1,
            )
        }
        ordered = controller.order_case_b_candidates(
            [upstream_metric, bottleneck_metric],
            metrics_by_id,
            current_route_index=0,
            max_active=3,
            segment_metrics_by_id=segment_metrics,
        )
        self.assertEqual([metric.movement.movement_id for metric in ordered[:2]], [bottleneck.movement_id, upstream.movement_id])
        case_b = controller.case_b_evaluation(
            bottleneck_metric,
            metrics_by_id,
            {upstream.movement_id: 50.0, bottleneck.movement_id: 60.0},
            controller.metric_ta(bottleneck_metric, {bottleneck.movement_id: 60.0}, previous_phase=bottleneck.selected_red_phase),
            segment_metrics,
        )
        self.assertEqual(case_b.case_b_source, "runtime_tau_segment")
        self.assertEqual(case_b.case_b_segment_id, "S_TEST")
        self.assertAlmostEqual(float(case_b.case_b_segment_fill), 0.80)

    def test_case_b_runtime_tau_miss_stays_case_a(self):
        upstream = self.stage1.movements[0]
        bottleneck = self.stage1.movements[1]
        stage1 = replace(
            self.stage1,
            case_b_candidates=(
                self.runtime.B4CaseBCandidate(
                    segment_id="S_TEST",
                    bottleneck_movement_id=bottleneck.movement_id,
                    upstream_movement_id=upstream.movement_id,
                    L_b0_m=100.0,
                    lane_drop_delta=1,
                    q_avg_B0=1.0,
                    q_max_B0=2.0,
                    tQ_hist_B0=4.0,
                    lambda_B0=100.0,
                    fill_B0=0.80,
                    speed_B0=10.0,
                    mapping_status="mapped",
                    tau_default=0.75,
                    case_b_prior_risk=True,
                ),
            ),
        )
        controller = self.runtime.B4RuntimeController(traci=FakeTraci(), stage1=stage1, run_id="contract")
        bottleneck_metric = replace(self.metric(bottleneck, queue_m_proxy=20.0), queue_confidence=self.runtime.QUEUE_PROXY_CONFIDENCE)
        case_b = controller.case_b_evaluation(
            bottleneck_metric,
            {
                upstream.movement_id: replace(self.metric(upstream, queue_m_proxy=10.0), queue_confidence=self.runtime.QUEUE_PROXY_CONFIDENCE),
                bottleneck.movement_id: bottleneck_metric,
            },
            {upstream.movement_id: 50.0, bottleneck.movement_id: 60.0},
            controller.metric_ta(bottleneck_metric, {bottleneck.movement_id: 60.0}, previous_phase=bottleneck.selected_red_phase),
        )
        self.assertEqual(case_b.case_b_source, "not_case_b")
        self.assertEqual(case_b.TA_case, "caseA")

    def test_case_b_stale_runtime_uses_b0_prior(self):
        upstream = self.stage1.movements[0]
        bottleneck = self.stage1.movements[1]
        candidate = self.runtime.B4CaseBCandidate(
            segment_id="S_TEST",
            bottleneck_movement_id=bottleneck.movement_id,
            upstream_movement_id=upstream.movement_id,
            L_b0_m=100.0,
            lane_drop_delta=1,
            q_avg_B0=1.0,
            q_max_B0=2.0,
            tQ_hist_B0=4.0,
            lambda_B0=100.0,
            fill_B0=0.80,
            speed_B0=10.0,
            mapping_status="mapped_route_span_proxy",
            tau_default=0.75,
            case_b_prior_risk=True,
            segment_lanes=("lane_a",),
            case_b_runtime_enabled=True,
        )
        stage1 = replace(self.stage1, case_b_candidates=(candidate,))
        controller = self.runtime.B4RuntimeController(traci=FakeTraci(), stage1=stage1, run_id="contract")
        bottleneck_metric = replace(self.metric(bottleneck, queue_m_proxy=0.0), queue_confidence=self.runtime.QUEUE_STALE_CONFIDENCE)
        case_b = controller.case_b_evaluation(
            bottleneck_metric,
            {
                upstream.movement_id: replace(self.metric(upstream, queue_m_proxy=0.0), queue_confidence=self.runtime.QUEUE_STALE_CONFIDENCE),
                bottleneck.movement_id: bottleneck_metric,
            },
            {upstream.movement_id: 50.0, bottleneck.movement_id: 60.0},
            controller.metric_ta(bottleneck_metric, {bottleneck.movement_id: 60.0}, previous_phase=bottleneck.selected_red_phase),
        )
        self.assertEqual(case_b.case_b_source, "b0_prior")

    def test_case_b_same_tls_upstream_is_deferred_after_bottleneck_phase_change(self):
        candidate = next(item for item in self.stage1.case_b_candidates if item.segment_id == "S10")
        self.assertTrue(candidate.same_tls_chain)
        bottleneck = next(item for item in self.stage1.movements if item.movement_id == candidate.bottleneck_movement_id)
        upstream = next(item for item in self.stage1.movements if item.movement_id == candidate.upstream_movement_id)
        self.assertNotEqual(bottleneck.selected_green_phase, upstream.selected_green_phase)
        traci = FakeTraci()
        traci.vehicle.vehicles[self.stage1.ev_id] = {
            "edge": upstream.from_edge,
            "lane": f"{upstream.from_edge}_0",
            "route_index": upstream.route_order_index,
            "lane_position": 0.0,
            "speed": 0.0,
        }
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        original = self.runtime.movement_runtime_metrics

        def fake_metrics(_traci, candidate_movement, _thresholds):
            is_chain = candidate_movement.movement_id in {candidate.bottleneck_movement_id, candidate.upstream_movement_id}
            metric = self.metric(candidate_movement, candidate=is_chain, queue_m_proxy=220.0 if is_chain else 0.0)
            return replace(metric, queue_confidence=self.runtime.QUEUE_PROXY_CONFIDENCE)

        self.runtime.movement_runtime_metrics = fake_metrics
        try:
            events = controller.handle_stage3(700.0, controller.ev_state())
        finally:
            self.runtime.movement_runtime_metrics = original
        phase_changes = [event for event in events if event["action_type"] == "phase_change_target_green"]
        deferred = [event for event in events if event["action_type"] == "case_b_same_tls_deferred"]
        self.assertTrue(any(event["movement_id"] == bottleneck.movement_id for event in phase_changes))
        self.assertTrue(any(event["movement_id"] == upstream.movement_id for event in deferred))
        self.assertEqual(deferred[0]["case_b_same_tls_policy"], "bottleneck_first_defer_upstream_same_tls")
        self.assertEqual(deferred[0]["case_b_source"], "runtime_tau_movement")

    def test_stage3_same_tls_downstream_flush_cycles_when_ev_is_stopped(self):
        same_tls_pair = [
            movement
            for movement in self.stage1.movements
            if movement.tls_id == "joinedS_11346754524_11346754527_7335400049_cluster_11346754525_11346754526_2784736947_414685366_#2more"
        ]
        self.assertGreaterEqual(len(same_tls_pair), 2)
        same_tls_pair.sort(key=lambda movement: movement.route_order_index)
        current, downstream = same_tls_pair[0], same_tls_pair[1]
        traci = FakeTraci()
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        controller.active_controls[current.movement_id] = self.runtime.ActiveControl(
            movement_id=current.movement_id,
            tls_id=current.tls_id,
            previous_phase=current.selected_red_phase,
            target_phase=current.selected_green_phase,
            started_at=10.0,
            deadline=20.0,
            route_order_index=current.route_order_index,
        )
        ev_state = self.runtime.EVState(
            present=True,
            departed=True,
            arrived=False,
            vehicle_id=self.stage1.ev_id,
            edge_id=current.from_edge,
            lane_id=f"{current.from_edge}_0",
            route_index=current.route_order_index,
            speed_mps=0.0,
            speed_kmh=0.0,
        )
        events = controller.restore_passed_or_expired_controls(20.0, ev_state)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action_type"], "downstream_flush_same_tls")
        self.assertEqual(events[0]["movement_id"], downstream.movement_id)
        self.assertEqual(events[0]["target_phase"], downstream.selected_green_phase)

    def test_stage3_same_lane_blocker_flush_cycles_before_extending_ev_only_green(self):
        movement = next(item for item in self.stage1.movements if item.from_edge == "781985787#0")
        self.assertEqual(movement.ev_route_link_indices, (18,))
        self.assertEqual(movement.parallel_through_link_indices, (14, 15, 16, 18))
        self.assertEqual(movement.same_lane_blocking_link_indices, (15, 16, 17))
        self.assertEqual(movement.selected_green_phase, 4)
        self.assertEqual(movement.selected_flush_phase, 2)
        self.assertTrue(movement.same_lane_blocker_flush_available)
        traci = FakeTraci()
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        controller.active_controls[movement.movement_id] = self.runtime.ActiveControl(
            movement_id=movement.movement_id,
            tls_id=movement.tls_id,
            previous_phase=movement.selected_red_phase,
            target_phase=movement.selected_green_phase,
            started_at=100.0,
            deadline=130.0,
            route_order_index=movement.route_order_index,
        )
        ev_state = self.runtime.EVState(
            present=True,
            departed=True,
            arrived=False,
            vehicle_id=self.stage1.ev_id,
            edge_id="347237859#4",
            lane_id="347237859#4_1",
            route_index=movement.route_order_index - 2,
            speed_mps=0.0,
            speed_kmh=0.0,
        )
        flush_events = controller.restore_passed_or_expired_controls(130.0, ev_state)
        self.assertEqual(len(flush_events), 1)
        self.assertEqual(flush_events[0]["action_type"], "same_lane_blocker_flush")
        self.assertEqual(flush_events[0]["target_phase"], 2)
        self.assertEqual(flush_events[0]["same_lane_blocking_linkIndex"], "15 16 17")
        self.assertEqual(flush_events[0]["control_strategy"], "route_green_with_same_lane_blocker_flush")

        return_events = controller.restore_passed_or_expired_controls(140.0, ev_state)
        self.assertEqual(len(return_events), 1)
        self.assertEqual(return_events[0]["action_type"], "return_to_target_green")
        self.assertEqual(return_events[0]["target_phase"], 4)

    def test_stage3_does_not_evaluate_before_ev_merges(self):
        traci = FakeTraci()
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        original = self.runtime.movement_runtime_metrics

        def fail_if_called(*_args, **_kwargs):
            self.fail("Stage3 must not read movement metrics before EV merges")

        self.runtime.movement_runtime_metrics = fail_if_called
        try:
            ev_state = self.runtime.EVState(
                present=True,
                departed=True,
                arrived=False,
                vehicle_id=self.stage1.ev_id,
                edge_id=self.stage1.route_edges[0],
                route_index=0,
                speed_mps=8.0,
                speed_kmh=28.8,
            )
            self.assertEqual(controller.handle_stage3(600.0, ev_state), [])
        finally:
            self.runtime.movement_runtime_metrics = original

    def test_stage3_skips_tls_owned_by_stage2_hold(self):
        base_movement = self.stage1.movements[0]
        merge_owned_movement = replace(base_movement, tls_id=self.stage1.departure.merge_control_tls)
        stage1 = replace(self.stage1, movements=(merge_owned_movement,), max_active_movements=1)
        traci = FakeTraci()
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=stage1, run_id="contract")
        controller.stage2_hold_active = True
        original = self.runtime.movement_runtime_metrics

        def fake_metrics(_traci, candidate_movement, _thresholds):
            return self.metric(candidate_movement)

        self.runtime.movement_runtime_metrics = fake_metrics
        try:
            ev_state = self.runtime.EVState(
                present=True,
                departed=True,
                arrived=False,
                vehicle_id=stage1.ev_id,
                edge_id=merge_owned_movement.from_edge,
                lane_id=f"{merge_owned_movement.from_edge}_0",
                route_index=merge_owned_movement.route_order_index,
                speed_mps=0.0,
                speed_kmh=0.0,
            )
            events = controller.handle_stage3(700.0, ev_state)
        finally:
            self.runtime.movement_runtime_metrics = original
        self.assertEqual(events, [])
        self.assertEqual(traci.trafficlight.actions, [])

    def test_stage3_low_speed_candidate_selected_when_fill_below_threshold(self):
        movement = self.stage1.movements[0]
        traci = FakeTraci()
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        original = self.runtime.movement_runtime_metrics

        def fake_metrics(_traci, candidate_movement, _thresholds):
            return self.metric(
                candidate_movement,
                candidate=(candidate_movement == movement),
                queue_m_proxy=10.0,
                local_fill_100m=0.10,
                approach_speed_kmh=10.0,
                trigger_reason="low_speed",
            )

        self.runtime.movement_runtime_metrics = fake_metrics
        try:
            ev_state = self.runtime.EVState(
                present=True,
                departed=True,
                arrived=False,
                vehicle_id=self.stage1.ev_id,
                edge_id=movement.from_edge,
                lane_id=f"{movement.from_edge}_0",
                route_index=movement.route_order_index,
                speed_mps=0.0,
                speed_kmh=0.0,
            )
            events = controller.handle_stage3(700.0, ev_state)
        finally:
            self.runtime.movement_runtime_metrics = original
        change = next(event for event in events if event["action_type"] == "phase_change_target_green")
        self.assertEqual(change["movement_id"], movement.movement_id)
        self.assertEqual(change["trigger_reason"], "low_speed")
        self.assertEqual(change["ta_triggered"], True)

    def test_stage3_local_fill_candidate_selected_when_speed_above_threshold(self):
        movement = self.stage1.movements[0]
        traci = FakeTraci()
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        original = self.runtime.movement_runtime_metrics

        def fake_metrics(_traci, candidate_movement, _thresholds):
            return self.metric(
                candidate_movement,
                candidate=(candidate_movement == movement),
                queue_m_proxy=60.0,
                local_fill_100m=0.60,
                approach_speed_kmh=30.0,
                trigger_reason="local_fill",
            )

        self.runtime.movement_runtime_metrics = fake_metrics
        try:
            ev_state = self.runtime.EVState(
                present=True,
                departed=True,
                arrived=False,
                vehicle_id=self.stage1.ev_id,
                edge_id=movement.from_edge,
                lane_id=f"{movement.from_edge}_0",
                route_index=movement.route_order_index,
                speed_mps=0.0,
                speed_kmh=0.0,
            )
            events = controller.handle_stage3(700.0, ev_state)
        finally:
            self.runtime.movement_runtime_metrics = original
        change = next(event for event in events if event["action_type"] == "phase_change_target_green")
        self.assertEqual(change["movement_id"], movement.movement_id)
        self.assertEqual(change["trigger_reason"], "local_fill")
        self.assertEqual(change["ta_triggered"], True)

    def test_stage3_active_movement_cap_blocks_new_candidate(self):
        movement = self.stage1.movements[0]
        traci = FakeTraci()
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        for active_movement in self.stage1.movements[: self.stage1.max_active_movements]:
            controller.active_controls[active_movement.movement_id] = self.runtime.ActiveControl(
                movement_id=active_movement.movement_id,
                tls_id=active_movement.tls_id,
                previous_phase=active_movement.selected_red_phase,
                target_phase=active_movement.selected_green_phase,
                started_at=690.0,
                deadline=900.0,
                route_order_index=999,
            )
        original = self.runtime.movement_runtime_metrics

        def fake_metrics(_traci, candidate_movement, _thresholds):
            return self.metric(candidate_movement, candidate=(candidate_movement == movement))

        self.runtime.movement_runtime_metrics = fake_metrics
        try:
            ev_state = self.runtime.EVState(
                present=True,
                departed=True,
                arrived=False,
                vehicle_id=self.stage1.ev_id,
                edge_id=movement.from_edge,
                lane_id=f"{movement.from_edge}_0",
                route_index=movement.route_order_index,
                speed_mps=0.0,
                speed_kmh=0.0,
            )
            events = controller.handle_stage3(700.0, ev_state)
        finally:
            self.runtime.movement_runtime_metrics = original
        self.assertNotIn("phase_change_target_green", [event["action_type"] for event in events])
        self.assertEqual(len(controller.active_controls), self.stage1.max_active_movements)

    def test_stage3_far_ahead_expired_control_restores_previous_phase(self):
        movement = self.stage1.movements[-1]
        traci = FakeTraci()
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        controller.active_controls[movement.movement_id] = self.runtime.ActiveControl(
            movement_id=movement.movement_id,
            tls_id=movement.tls_id,
            previous_phase=movement.selected_red_phase,
            target_phase=movement.selected_green_phase,
            started_at=100.0,
            deadline=130.0,
            route_order_index=movement.route_order_index,
        )
        controller.ev_distance_to_movement = lambda _ev_state, _movement: self.runtime.DEFAULT_NEAR_HOLD_DISTANCE_M + 1.0
        ev_state = self.runtime.EVState(
            present=True,
            departed=True,
            arrived=False,
            vehicle_id=self.stage1.ev_id,
            edge_id=self.stage1.departure.mainline_target_edge,
            lane_id=f"{self.stage1.departure.mainline_target_edge}_0",
            route_index=1,
            speed_mps=8.0,
            speed_kmh=28.8,
        )
        events = controller.restore_passed_or_expired_controls(140.0, ev_state)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action_type"], "restore_previous_phase")
        self.assertEqual(events[0]["control_mode"], "restore_far_ahead_after_max_hold")
        self.assertEqual(events[0]["trigger_reason"], "max_hold_elapsed_far_ahead")

    def test_event_schema_contains_stage1_fields_and_runtime_comparison_fields(self):
        movement = self.stage1.movements[0]
        metric = self.metric(movement)
        row = self.runtime.event_row(
            time=600,
            stage="stage3",
            action_type="target_green",
            movement=movement,
            metrics=metric,
            target_phase=movement.selected_green_phase,
            previous_phase=movement.selected_red_phase,
            ev_distance_m=123.4,
            safety_status="stage1_selected_phase_mvp",
        )
        for field in self.runtime.REQUIRED_STAGE1_EVENT_FIELDS:
            self.assertIn(field, row)
        for field in ["local_fill_80m", "local_fill_120m", "queue_m_proxy", "tE_sec", "stage2_hold_status"]:
            self.assertIn(field, row)
        for field in ["TA_proxy_sec", "tQ_sec", "b0_q_avg_proxy_veh", "ta_triggered", "ta_formula", "ta_input_source"]:
            self.assertIn(field, row)
        for field in ["queue_source", "case_b_source", "tS_source", "TA_case", "TA_upstream_sec", "TA_bottleneck_sec"]:
            self.assertIn(field, row)
        for field in ["case_b_mapping_status", "case_b_segment_id", "case_b_segment_queue_m_proxy", "case_b_segment_fill", "case_b_same_tls_policy"]:
            self.assertIn(field, row)
        for field in ["D_merge_m", "n_occ_runtime_veh", "T_hold_proxy_sec", "stage2_formula", "stage2_measurement_source"]:
            self.assertIn(field, row)
        for field in ["monitor_local_fill_mean", "termination_reason"]:
            self.assertIn(field, row)

    def test_stage3_ta_gate_blocks_candidate_when_ta_positive(self):
        movement = self.stage1.movements[0]
        traci = FakeTraci()
        traci.vehicle.vehicles[self.stage1.ev_id] = {
            "edge": self.stage1.departure.mainline_target_edge,
            "lane": f"{self.stage1.departure.mainline_target_edge}_0",
            "route_index": 1,
            "lane_position": 0.0,
            "speed": 8.0,
        }
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        original = self.runtime.movement_runtime_metrics

        def fake_metrics(_traci, candidate_movement, _thresholds):
            return self.metric(candidate_movement, candidate=(candidate_movement == movement))

        self.runtime.movement_runtime_metrics = fake_metrics
        try:
            events = controller.handle_stage3(700.0, controller.ev_state())
        finally:
            self.runtime.movement_runtime_metrics = original
        self.assertIn("trigger_evaluation", [event["action_type"] for event in events])
        self.assertNotIn("phase_change_target_green", [event["action_type"] for event in events])
        eval_event = next(event for event in events if event["action_type"] == "trigger_evaluation")
        self.assertEqual(eval_event["ta_triggered"], False)
        self.assertGreater(float(eval_event["TA_proxy_sec"]), 0.0)

    def test_stage3_ta_gate_allows_candidate_when_ta_nonpositive(self):
        movement = self.stage1.movements[0]
        traci = FakeTraci()
        traci.vehicle.vehicles[self.stage1.ev_id] = {
            "edge": movement.from_edge,
            "lane": f"{movement.from_edge}_0",
            "route_index": movement.route_order_index,
            "lane_position": 0.0,
            "speed": 0.0,
        }
        controller = self.runtime.B4RuntimeController(traci=traci, stage1=self.stage1, run_id="contract")
        original = self.runtime.movement_runtime_metrics

        def fake_metrics(_traci, candidate_movement, _thresholds):
            return self.metric(candidate_movement, candidate=(candidate_movement == movement))

        self.runtime.movement_runtime_metrics = fake_metrics
        try:
            events = controller.handle_stage3(700.0, controller.ev_state())
        finally:
            self.runtime.movement_runtime_metrics = original
        self.assertIn("phase_change_target_green", [event["action_type"] for event in events])
        change = next(event for event in events if event["action_type"] == "phase_change_target_green")
        self.assertEqual(change["ta_triggered"], True)
        self.assertLessEqual(float(change["TA_proxy_sec"]), 0.0)

    def test_bo_smoke_phase_defaults_and_result_schema_fields(self):
        config = self.runtime.B4RuntimePhaseConfig.from_phase("bo-smoke")
        self.assertEqual(config.phase, "bo-smoke")
        self.assertEqual(config.ev_departure_policy, "fixed")
        self.assertEqual(config.ev_depart_sec, 600.0)
        self.assertFalse(config.ev_depart_randomized)
        self.assertFalse(config.final_validation_random_departure_implemented)
        self.assertEqual(config.pre_ev_reference_window, (540.0, 600.0))
        self.assertEqual(config.hard_max_sim_time, 1800.0)
        self.assertEqual(config.ev_stuck_duration_sec, 120.0)
        for field in [
            "phase",
            "ev_departure_policy",
            "ev_depart_sec",
            "ev_depart_randomized",
            "termination_reason",
            "recovery_detected",
            "objective_includes_recovery",
            "emergency_seen_by_controller",
            "emergency_tripinfo_found",
        ]:
            self.assertIn(field, self.runtime.EXPERIMENT_RESULT_FIELDS)

    def test_recovery_requires_three_consecutive_samples_and_is_not_objective(self):
        traci = FakeTraci()
        config = self.runtime.B4RuntimePhaseConfig.bo_smoke()
        monitor = self.runtime.B4RuntimeMonitor(
            traci=traci,
            stage1=self.stage1,
            config=config,
            monitor_lanes=("monitor_0",),
            mode="B4",
        )
        monitor.pre_samples = [
            {"local_fill_mean": 0.10, "speed_mean_kmh": 30.0, "waiting_mean": 2.0, "halting_count": 0}
        ]
        monitor.ev_arrival_time = 610.0
        monitor.next_recovery_sample_time = 610.0
        traci.lane.set_lane("monitor_0", [], speed_mps=8.0)
        ev_state = self.runtime.EVState(True, True, True, self.stage1.ev_id, "619147738#1", "619147738#1_0", 60, speed_kmh=0.0)
        events = []
        for now in [610.0, 620.0, 630.0]:
            new_events, should_stop = monitor.observe(now, ev_state)
            events.extend(new_events)
        self.assertTrue(monitor.recovery_detected)
        self.assertIn("recovery_detected", [event["action_type"] for event in events])
        fields = monitor.as_result_fields(emergency_tripinfo_found=True)
        self.assertFalse(fields["objective_includes_recovery"])
        self.assertEqual(fields["post_ev_recovery_duration_sec"], 20.0)

    def test_stuck_detection_records_emergency_diagnostics(self):
        traci = FakeTraci()
        config = self.runtime.B4RuntimePhaseConfig.bo_smoke()
        monitor = self.runtime.B4RuntimeMonitor(
            traci=traci,
            stage1=self.stage1,
            config=config,
            monitor_lanes=("monitor_0",),
            mode="B4",
        )
        ev_state = self.runtime.EVState(
            present=True,
            departed=True,
            arrived=False,
            vehicle_id=self.stage1.ev_id,
            edge_id="347237859#3",
            lane_id="347237859#3_1",
            route_index=47,
            speed_mps=0.0,
            speed_kmh=0.0,
        )
        monitor.observe(600.0, ev_state)
        events, should_stop = monitor.observe(720.0, ev_state)
        self.assertTrue(should_stop)
        self.assertEqual(monitor.termination_reason, "emergency_stuck")
        self.assertIn("emergency_stuck", [event["action_type"] for event in events])
        fields = monitor.as_result_fields(emergency_tripinfo_found=False)
        self.assertTrue(fields["emergency_seen_by_controller"])
        self.assertEqual(fields["emergency_last_edge"], "347237859#3")
        self.assertEqual(fields["emergency_last_route_index"], 47)
        self.assertEqual(fields["emergency_stuck_duration_sec"], 120.0)

    def test_runner_builds_single_seed_aa_tasks_without_fcd_or_manifest_write(self):
        before = B04_MANIFEST.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            tasks = self.runner.build_tasks(run_id="contract", modes=("B004", "B04", "B4"), run_root=Path(tmp))
            self.assertEqual([task.mode for task in tasks], ["B004", "B04", "B4"])
            self.assertEqual({task.seed for task in tasks}, {1})
            self.assertEqual(tasks[0].parameter_id, "free_emv_analytic_50kmh")
            self.assertEqual(tasks[2].parameter_id, "B4_MVP_DEFAULT")
            for task in tasks:
                if task.mode == "B004":
                    with self.assertRaises(Exception):
                        self.runner.write_sumo_config(task)
                    continue
                self.assertIn("B04_aa_balanced_growth", task.background_route.name)
                paths = self.runner.write_sumo_config(task)
                cfg = paths["sumocfg"].read_text(encoding="utf-8")
                add = paths["additional"].read_text(encoding="utf-8")
                self.assertIn("edgeData", add)
                self.assertIn("laneData", add)
                self.assertIn('<end value="1800.0"/>', cfg)
                self.assertNotIn("fcd-output", cfg)
                self.assertNotIn("fcd-output", add)
                fcd_paths = self.runner.write_sumo_config(task, emit_fcd=True)
                fcd_cfg = fcd_paths["sumocfg"].read_text(encoding="utf-8")
                self.assertIn("fcd-output", fcd_cfg)
                self.assertIn("fcd-output.geo", fcd_cfg)
                self.assertIn("fcd-output.distance", fcd_cfg)
                self.assertIn("device.fcd.period", fcd_cfg)
                self.assertIn("device.fcd.begin", fcd_cfg)
                self.assertEqual(fcd_paths["fcd"].name, "fcd.xml")
        after = B04_MANIFEST.read_text(encoding="utf-8")
        self.assertEqual(before, after)
        with self.assertRaises(Exception):
            self.runner.build_tasks(run_id="bad", modes=("B4",), seed=2)
        alias = self.runner.parse_modes("B0,B4")
        self.assertEqual(alias, ("B04", "B4"))

    def test_runner_metric_output_names_are_fixed(self):
        with tempfile.TemporaryDirectory() as run_tmp, tempfile.TemporaryDirectory() as metrics_tmp:
            tasks = self.runner.build_tasks(run_id="contract", modes=("B004", "B04", "B4"), run_root=Path(run_tmp))
            stage1 = self.runtime.B4Stage1Inputs.load()
            self.runner.build_b004_free_reference(stage1)
            rows = [
                {"run_id": "contract", "mode": "B004", "scenario_name": "emv_free_flow_fire_station_to_seoul_station_front", "parameter_id": "free_emv_analytic_50kmh", "T_actual_EMV_sec": 10.0, "T_free_EMV_sec": 10.0, "d_EMV_sec": 0.0, "objective_score": 0.0},
                {"run_id": "contract", "mode": "B04", "scenario_name": "compact_v9_B04_AA_real_demand", "parameter_id": "no_control", "T_actual_EMV_sec": 100.0, "T_free_EMV_sec": 10.0, "d_EMV_sec": 90.0, "d_veh_sec": 1.0, "objective_score": 1801.0},
                {"run_id": "contract", "mode": "B4", "scenario_name": "compact_v9_B04_AA_real_demand", "parameter_id": "B4_MVP_DEFAULT", "T_actual_EMV_sec": 90.0, "T_free_EMV_sec": 10.0, "d_EMV_sec": 80.0, "d_veh_sec": 2.0, "objective_score": 1602.0},
            ]
            outputs = self.runner.write_metric_outputs(rows, tasks, stage1, Path(metrics_tmp))
            self.assertEqual(
                set(outputs),
                {
                    "experiment_results_csv",
                    "signal_events_csv",
                    "compare_b0_b4_csv",
                    "b004_b04_b4_comparison_csv",
                    "experiment_summary_json",
                    "route_visualization_html",
                    "route_visualization_json",
                    "b4_ta_b0_measurement_review_html",
                    "b004_free_time_reference_json",
                    "b004_vehicle_free_times_csv",
                },
            )
            for rel_path in outputs.values():
                self.assertTrue((PROJECT_ROOT / rel_path).is_file() or Path(metrics_tmp, Path(rel_path).name).is_file())

    def test_b004_free_reference_is_emv_only_fire_station_to_seoul_station_front(self):
        reference = self.runner.build_b004_free_reference(self.stage1)
        self.assertEqual(reference["mode"], "B004")
        self.assertEqual(reference["scenario_name"], "emv_free_flow_fire_station_to_seoul_station_front")
        self.assertEqual(reference["free_time_method"], "analytic_50kmh")
        self.assertEqual(reference["start_edge"], "420331801#1")
        self.assertEqual(reference["merge_edge"], "-174870621#8")
        self.assertEqual(reference["target_edge"], "619147738#1")
        self.assertGreater(reference["route_length_m"], 0)
        self.assertAlmostEqual(reference["T_free_EMV_sec"], reference["route_length_m"] / (50.0 / 3.6), places=5)
        self.assertGreaterEqual(reference["veh_eval_count"], 1)

    def test_objective_score_formula(self):
        self.assertAlmostEqual(self.runner.objective_score(10.0, 2.5), 202.5)


if __name__ == "__main__":
    unittest.main()
