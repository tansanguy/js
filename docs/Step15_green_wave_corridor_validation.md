# Step 15 Green Wave Corridor Validation

## Purpose

This step verifies a corridor-wide B1 Green Wave smoke using the single route that uses the main spine road most strongly: `ER_ACC_019`.

This is not B2, Bayesian Optimization, or a multi-seed performance evaluation.

## Route Choice

- route_id: `ER_ACC_019`
- route_length_m: `3584.99`
- spine_length_m: `3260.42`
- spine_length_ratio: `0.909464`
- route_tls_count: `7`

`ER_ACC_019` was selected because it has the largest spine length among the 19 spine-v2 routes and previously had no emergency teleport under B0/B1 smoke.

## Smoke Result

- final_status: `WARNING`
- loaded_terminal_count: `20`
- route_relevant_terminal_count: `7`
- not_on_route_terminal_count: `13`
- controlled_tls_count: `5`
- skipped_tls_count: `2`
- intervention_count: `5`
- emergency_arrived: `True`
- emergency_teleport: `False`
- route_error_count: `0`
- emergency_travel_time: `204.0`
- background_teleport_ratio: `0.003058`

## Interpretation

All 20 priority terminal candidates were loaded as controller candidates. The controller only acted on route-relevant TLS. Terminals outside `ER_ACC_019` were not forced and were logged as `not_on_route`.

This verifies corridor terminal loading plus route-relevant Green Wave behavior for the route that uses the main spine the most. It does not claim that all 20 signals were physically changed in a single run.

## Outputs

- summary: `results/metrics/b1_green_wave_corridor_er_acc_019_smoke_summary.csv/json`
- signal events: `results/metrics/b1_green_wave_corridor_er_acc_019_signal_events.csv`
- terminal coverage: `results/metrics/b1_green_wave_corridor_er_acc_019_terminal_coverage.csv`
- run dir: `runs/b1_green_wave_corridor_er_acc_019/`
