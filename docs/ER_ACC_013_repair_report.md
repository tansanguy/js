# ER_ACC_013 Repair Report

## Decision

`EXCLUDE_PRELIMINARY`

## Candidates

[
  {
    "candidate_id": "no_net_patch_background_jam_diagnostic",
    "candidate_type": "demand_interaction_diagnosis",
    "description": "Connection and permissions are valid; emergency-only passes, B0 fails under local background jam. No net patch selected.",
    "net_variant": "data_prepared/net/jungbu_ellipse_passenger.net.xml",
    "route_variant": "original ER_ACC_013",
    "safe_to_apply": false,
    "selected": false,
    "decision": "EXCLUDE_PRELIMINARY",
    "reason": "background demand jam on a valid internal TLS connection"
  }
]

## Verification

[
  {
    "candidate_id": "original_route_emergency_only",
    "candidate_type": "demand_interaction_diagnosis",
    "net_variant": "data_prepared/net/jungbu_ellipse_passenger.net.xml",
    "route_variant": "data_prepared/net_repair/er_acc_013/er_acc_013_original_emergency_only.rou.xml",
    "same_start": true,
    "same_target": true,
    "sumo_exit_code": 0,
    "emergency_departed": true,
    "emergency_arrived": true,
    "emergency_teleport": false,
    "emergency_teleport_evidence": [],
    "route_error_count": 0,
    "emergency_travel_time": 159.0,
    "emergency_waiting_time": 0.0,
    "background_departed": 0,
    "background_arrived": 0,
    "background_teleported": 0,
    "sim_end_time": 159.0,
    "run_dir": "runs/net_repair_er_acc_013/emergency_only_original",
    "sumocfg": "runs/net_repair_er_acc_013/emergency_only_original/scenario.sumocfg",
    "stderr_log": "runs/net_repair_er_acc_013/emergency_only_original/sumo_stderr.log",
    "tripinfo": "runs/net_repair_er_acc_013/emergency_only_original/tripinfo.xml",
    "final_status": "PASS"
  },
  {
    "candidate_id": "original_route_b0_0p15_existing",
    "candidate_type": "baseline_reference",
    "net_variant": "data_prepared/net/jungbu_ellipse_passenger.net.xml",
    "route_variant": "data_prepared/routes/emergency_routes_spine_v2.csv",
    "same_start": true,
    "same_target": true,
    "sumo_exit_code": 0,
    "emergency_departed": true,
    "emergency_arrived": true,
    "emergency_teleport": true,
    "route_error_count": 0,
    "emergency_travel_time": 1394.0,
    "background_departed": 654,
    "background_arrived": 654,
    "background_teleported": 4,
    "run_dir": "runs/b0_baseline_19route_smoke/ER_ACC_013",
    "final_status": "FAIL"
  }
]

Original net, route CSV, and 0.15x background demand were not overwritten.
