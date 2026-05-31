# 05 Theta Check Simulation

This folder is an independent final-validation pipeline for checking the selected B2 theta on the `b0_valid_18` emergency routes. It does not modify or depend on the `02_simulation` pipeline outputs.

## Inputs

Default route input is the local 18-route snapshot:

- `05_theta_check_simulation/routes/b0_valid_18_routes.csv`
- `05_theta_check_simulation/routes/b0_valid_18_routes_manifest.json`

`ER_ACC_013` is excluded because it had prior B0 emergency teleport issues. The original 19-route source remains in `data_prepared/routes/emergency_routes_spine_v2.csv`.

Default simulation inputs:

- net: `data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml`
- background demand: `data_prepared/demand/background_routes_am_imputed_a17_a19_warm0p15_sustain0p05_seed002_sustained_3600.rou.xml`
- TLS audit: `data_prepared/signals/tls_phase_audit_spine_v2.csv`
- priority terminals: `data_prepared/signals/priority_terminal_candidates.csv`
- B2 theta CSV: `configs/b2_parameter_sets.csv`

Final optimum validation uses the explicit one-row B2 parameter file:

- `05_theta_check_simulation/final_optimum_b2_parameter_sets.csv`
- `D_det=450`, `alpha=6`, `G_ext=51`, `T_change_sec=10`

## Pipeline

1. Route connectivity smoke

   Run each of the 18 routes once in `B00` free-flow mode. This checks route connectivity, emergency arrival, emergency teleport, and route errors without background demand.

   ```bash
   python 05_theta_check_simulation/parameter_sim.py \
     --modes B00 \
     --repeats 1 \
     --workers 6 \
     --output-prefix route_connectivity_smoke \
     --resume
   ```

2. Single-route functional smoke

   Run one route through `B00`, `B0`, and `B2` before launching the full batch.

   ```bash
   python 05_theta_check_simulation/parameter_sim.py \
     --routes ER_ACC_001 \
     --modes B00 B0 B2 \
     --repeats 1 \
     --workers 1 \
     --output-prefix parameter_sim_smoke \
     --resume
   ```

3. Full theta validation

   Run all 18 routes through `B00`, `B0`, and `B2`. Emergency departure time is generated deterministically per route/repeat from `--seed` and the `550~650` second window.

   ```bash
   python 05_theta_check_simulation/parameter_sim.py \
     --modes B00 B0 B2 \
     --routes-csv 05_theta_check_simulation/routes/b0_valid_18_routes.csv \
     --output-prefix parameter_sim \
     --b2-params configs/b2_parameter_sets.csv \
     --depart-min 550 \
     --depart-max 650 \
     --seed 20260531 \
     --repeats 1 \
     --workers 6 \
     --resume
   ```

4. Final optimum route screening

   Run the 18-route snapshot once with the final optimum theta and select three routes with the clearest valid B2 improvement.

   ```bash
   python 05_theta_check_simulation/parameter_sim.py \
     --modes B00 B0 B2 \
     --routes-csv 05_theta_check_simulation/routes/b0_valid_18_routes.csv \
     --b2-params 05_theta_check_simulation/final_optimum_b2_parameter_sets.csv \
     --depart-min 300 \
     --depart-max 2400 \
     --seed 20260531 \
     --repeats 1 \
     --workers 6 \
     --output-prefix final_optimum_route_screening \
     --resume
   ```

5. Final 30-repeat paired validation

   Run the selected routes with one B00 free-flow run and 30 paired B0/B2 repeats. B0 and B2 share the same route/repeat emergency departure time.

   ```bash
   python 05_theta_check_simulation/parameter_sim.py \
     --routes ER_ACC_010 ER_ACC_008 ER_ACC_012 \
     --modes B00 B0 B2 \
     --routes-csv 05_theta_check_simulation/routes/b0_valid_18_routes.csv \
     --b2-params 05_theta_check_simulation/final_optimum_b2_parameter_sets.csv \
     --depart-min 300 \
     --depart-max 2400 \
     --seed 20260531 \
     --repeats 30 \
     --b00-repeats 1 \
     --b0-repeats 30 \
     --b2-repeats 30 \
     --workers 3 \
     --output-prefix final_optimum_validation \
     --resume
   ```

   Generate the compact final report:

   ```bash
   python 05_theta_check_simulation/final_validation_report.py \
     --screening-results results/metrics/05_theta_check_simulation/final_optimum_route_screening/20260531T150223_686366Z0000/experiment_results.csv \
     --final-results results/metrics/05_theta_check_simulation/final_optimum_validation/20260531T151900_688506Z0000/experiment_results.csv \
     --output-dir results/metrics/05_theta_check_simulation/final_optimum_validation_report \
     --limit 3 \
     --screening-workers 6 \
     --final-workers 3
   ```

## Resume Behavior

The runner is designed to survive interruption.

- At startup, it writes `task_manifest.json`.
- Each task writes its own `task_status.json`.
- `--resume` skips tasks whose status is already `PASS`, `WARNING`, or `FAIL`.
- Tasks left as `RUNNING`, missing, or malformed are rerun.
- Aggregate CSV/JSON outputs are refreshed after every completed task.
- To force a clean run, use a new `--output-prefix` or pass a new explicit `--run-id`.

## Main Options

- `--modes B00 B0 B2`: choose simulation modes.
- `--routes ER_ACC_001 ER_ACC_002`: run only selected route IDs.
- `--routes-csv PATH`: use a different route CSV.
- `--exclude-routes ER_ACC_013`: exclude route IDs from the chosen CSV.
- `--b2-params PATH`: choose the B2 theta CSV.
- `--depart-min 550 --depart-max 650`: emergency departure sampling window.
- `--seed 20260531`: deterministic departure seed.
- `--repeats N`: repeat each route/mode.
- `--b00-repeats N`, `--b0-repeats N`, `--b2-repeats N`: override repeat count per mode.
- `--workers N`: parallel worker count.
- `--resume`: continue from existing completed task statuses.
- `--output-prefix NAME`: output namespace under this pipeline.

## Outputs

Run artifacts stay under:

- `runs/05_theta_check_simulation/{output_prefix}/{run_id}/`
- `results/metrics/05_theta_check_simulation/{output_prefix}/{run_id}/`
- `results/metrics/05_theta_check_simulation/{output_prefix}/latest.json`

Important result files:

- `task_manifest.json`: planned tasks, route, mode, repeat, parameter, depart time, and run directory.
- `experiment_results.csv`: task-level results.
- `route_summary.csv`: route-level B00/B0/B2 comparison summary.
- `score_components.csv`: same task-level rows for metric review.
- `experiment_summary.json`: run status, route improvements, failures, and signal burden summary.
- per-task `signal_events.csv`: B2 signal actions, if any.
- per-task `task_status.json`: resume marker and stored result row.

## Status Interpretation

- `PASS`: emergency arrived, no emergency teleport, no route errors, no background warning.
- `WARNING`: emergency succeeded, but background warnings such as background vehicle teleport occurred.
- `FAIL`: emergency did not depart/arrive, emergency teleported, SUMO/route error occurred, or task crashed.

For route connectivity smoke, expected success is `final_status: PASS` with all 18 routes arriving and no emergency teleport.

## Final Optimum Validation Result

Final validation run:

- screening run id: `20260531T150223_686366Z0000`
- final run id: `20260531T151900_688506Z0000`
- final task count: `183` (`B00=3`, `B0=90`, `B2=90`)
- final status: `PASS`
- selected routes: `ER_ACC_010`, `ER_ACC_008`, `ER_ACC_012`
- final compact report: `results/metrics/05_theta_check_simulation/final_optimum_validation_report/final_optimum_validation.md`

Summary:

| route | pairs | B0 mean sec | B2 mean sec | delta mean sec | improvement pct |
| --- | ---: | ---: | ---: | ---: | ---: |
| ER_ACC_008 | 30 | 232.77 | 132.47 | -100.30 | 42.79 |
| ER_ACC_010 | 30 | 257.80 | 148.33 | -109.47 | 42.72 |
| ER_ACC_012 | 30 | 225.93 | 144.50 | -81.43 | 35.83 |
| ALL | 90 | 238.83 | 141.77 | -97.07 | 40.44 |
