# Step 14 B1 Central Green Wave Controller v1

## Purpose

This step verifies that B1 controller parameters are actually used in TraCI decisions. It is not a B0/B1 performance evaluation, B2, Bayesian Optimization, or multi-seed experiment.

## Inputs

- net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- background: `data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml`
- emergency route: `ER_ACC_002`
- TLS audit: `data_prepared/signals/tls_phase_audit_spine_v2.csv`
- config: `configs/b1_priority_signal_config.json`

## Parameter Use

- D_det: `300` m, used for detection-distance trigger.
- v_e_policy: `current_speed_with_fallback`, current speed with fallback.
- fallback_v_e_mps: `8.33`.
- alpha: `1.2`, applied to ETA.
- G_ext: `30`, applied as green extension when current/next phase is emergency green.
- rho: `restore_original_program`, recorded as restore policy. Direct phase restore is not forced.
- tau: `1`, TraCI decision interval.
- t_lead: `30`, ETA trigger.
- metric_sample_interval: `10`, recorded in summary and events.

## Smoke Result

- final_status: `PASS`
- controller_started: `True`
- emergency_departed/arrived/teleport: `True` / `True` / `False`
- emergency_travel_time: `191.0`
- route_error_count: `0`
- intervention_count: `5`
- green_extension_count: `4`
- phase_switch_count: `1`
- restore_count: `5`
- skipped_tls_count: `3`
- failed_tls_count: `0`

## Safety

Pedestrian minimum walking time is not removed. Current implementation records `safety_placeholder_documented_not_optimized` and preserves conservative controller behavior: no direct phase-state rewrite, no yellow/clearance skip, and existing SUMO phase sequence only.

## Outputs

- summary CSV/JSON: `results/metrics/b1_green_wave_v1_er_acc_002_smoke_summary.csv/json`
- signal events: `results/metrics/b1_green_wave_v1_er_acc_002_signal_events.csv`
- log: `outputs/logs/step14_b1_green_wave_v1_er_acc_002.log`
